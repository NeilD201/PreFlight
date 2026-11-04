"""Meta Model API transport for Muse Spark 1.3 Contributor exclusively.

Model ID and Contributor pricing verified October 2026 against Meta's
official pricing page (quoted at tokensave.app/blog/muse-spark-1-3-api-pricing)
and OpenRouter (openrouter.ai/meta/muse-spark-1.3-contributor): Contributor
bills $0.10/1M input, $0.002/1M cached input and $0.20/1M output tokens.
Contributor billing is selected purely by the `-contributor` model ID; Meta
exposes no separate billing parameter. Requests that report no usable token
counts are recorded with zero tokens, never estimated.
"""
import math
import sys
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

MODEL_ID = 'muse-spark-1.3-contributor'
DEFAULT_MODEL = MODEL_ID
ENDPOINT = 'https://api.meta.ai/v1/chat/completions'
RESPONSES_ENDPOINT = 'https://api.meta.ai/v1/responses'
PRICING_USD_PER_M = {'input': 0.10, 'cached_input': 0.002, 'output': 0.20}

_action = ContextVar('meta_action', default='')
_recorder = None


@contextmanager
def meta_action(name):
    """Label Meta requests from one caller (review, chat, repair, connection)."""
    token = _action.set(name)
    try:
        yield
    finally:
        _action.reset(token)


def current_action():
    return _action.get()


def set_usage_recorder(recorder):
    """The app registers one persistent recorder; tests register a capture."""
    global _recorder
    _recorder = recorder


def clear_usage_recorder(recorder):
    """Release the recorder only when it is still ours."""
    global _recorder
    if _recorder is recorder or _recorder == recorder:
        _recorder = None


def _record(attempt):
    if _recorder is None:
        return
    try:
        _recorder(dict(attempt))
    except Exception as exc:
        print(f'Meta usage recording failed: {type(exc).__name__}', file=sys.stderr)


def estimate_cost_usd(input_tokens, output_tokens, cached_input_tokens=0, pricing=PRICING_USD_PER_M):
    """Estimated charge for reported tokens. Cached input bills separately."""
    billable = max(0, input_tokens - cached_input_tokens)
    return (billable * pricing['input'] + cached_input_tokens * pricing['cached_input'] +
            output_tokens * pricing['output']) / 1000000


def is_meta(model):
    return isinstance(model, str) and model.startswith('meta/')


def model_id(model):
    value = model.removeprefix('meta/') if isinstance(model, str) else ''
    if value != MODEL_ID:
        raise ValueError('PreFlight uses Muse Spark 1.3 Contributor exclusively.')
    return value


def _count(source, *names):
    for name in names:
        value = source.get(name)
        if (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value) and value >= 0 and int(value) == value):
            return int(value)
    return None


def post_meta(payload, key, *, timeout, progress=None, max_attempts=3, attempt_timeout=None, **unused):
    from .reviewer import AIError, _request
    model = model_id(payload['model'])
    if not isinstance(key, str) or not key:
        raise AIError('Add your Meta API key in Vector settings.')
    request = {'model': model, 'stream': False,
               'messages': [{'role': 'system', 'content': payload.get('system_instruction', '')},
                            {'role': 'user', 'content': payload.get('input', '')}],
               'max_completion_tokens': payload.get('generation_config', {}).get('max_output_tokens', 8000),
               'response_format': {'type': 'json_schema', 'json_schema': {
                   'name': 'pcb_preflight', 'schema': payload.get('response_format', {}).get('schema', {})}}}
    attempt = {'request_id': uuid.uuid4().hex, 'at': datetime.now(timezone.utc).isoformat(),
               'model': 'meta/' + model, 'action': current_action(), 'status': 'failed',
               'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
               'input_usd_per_m': PRICING_USD_PER_M['input'],
               'output_usd_per_m': PRICING_USD_PER_M['output'],
               'cached_usd_per_m': PRICING_USD_PER_M['cached_input']}

    def finish(status, usage):
        attempt.update(status=status, **usage)
        _record(attempt)

    if progress:
        progress('Asking Vector…')
    try:
        result = _request(ENDPOINT, request, {'Authorization': f'Bearer {key}'},
                          'meta', timeout=timeout, max_attempts=max_attempts,
                          progress=progress, attempt_timeout=attempt_timeout)
    except Exception:
        finish('failed', {})
        raise
    choices = result.get('choices')
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get('message') if isinstance(first, dict) else None
    raw_usage = result.get('usage') or {}
    usage = {}
    total_in = _count(raw_usage, 'prompt_tokens')
    total_out = _count(raw_usage, 'completion_tokens')
    details = raw_usage.get('prompt_tokens_details')
    cached = _count(details if isinstance(details, dict) else {}, 'cached_tokens')
    if cached is None:
        cached = _count(raw_usage, 'cached_tokens', 'cached_input_tokens')
    if total_in is not None:
        usage['total_input_tokens'] = total_in
    if total_out is not None:
        usage['total_output_tokens'] = total_out
    if total_in is not None and total_out is not None:
        usage['total_tokens'] = total_in + total_out
    elif _count(raw_usage, 'total_tokens') is not None:
        usage['total_tokens'] = _count(raw_usage, 'total_tokens')
    if cached is not None:
        usage['cached_input_tokens'] = cached
    recorded = {'input_tokens': total_in or 0, 'output_tokens': total_out or 0,
                'cached_input_tokens': cached or 0}
    if not isinstance(message, dict):
        finish('incomplete', recorded)
        raise AIError('Meta returned an invalid completion message. No partial result was accepted.')
    if message.get('refusal'):
        finish('incomplete', recorded)
        raise AIError('Meta declined this request. No edit or conclusion was accepted.')
    if first.get('finish_reason') == 'length':
        # Output-limit truncation: an identical retry would truncate again, so
        # this is not blindly retryable. The caller must narrow the request
        # scope (fewer findings per call) and run again. Partial content is
        # never accepted as a verdict.
        finish('incomplete', recorded)
        raise AIError('Meta stopped early: the answer exceeded the output limit. '
                      'No partial edit or conclusion was accepted. Narrow the request '
                      'scope and run again.',
                      diagnostics={'error_kind': 'truncated', 'retryable': False,
                                   'finish_reason': 'length'})
    if first.get('finish_reason') != 'stop' or message.get('tool_calls'):
        finish('incomplete', recorded)
        raise AIError('Meta did not finish its answer. No partial edit or conclusion was accepted.',
                      diagnostics={'error_kind': 'incomplete', 'retryable': False,
                                   'finish_reason': first.get('finish_reason')})
    content = message.get('content')
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content
                          if isinstance(part, dict) and part.get('type') == 'text')
    if not isinstance(content, str) or not content.strip():
        finish('incomplete', recorded)
        raise AIError('Meta returned an empty answer. No edit or conclusion was accepted.')
    finish('completed', recorded)
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': content}]}],
            'usage': usage}


def post_responses_image(*, image_b64, mime, prompt, key, timeout=120, max_output_tokens=1500,
                         effort='minimal', progress=None, max_attempts=2, images=None):
    """One bounded Responses API call with one image, or several when `images`
    (a list of {'b64', 'mime', 'label'}) is given (LiveScan batch vision).

    Uses the Contributor model exclusively. Reasoning tokens share the output
    budget, so callers must leave headroom (see dev.meta.ai/docs/reasoning).
    Returns {'status', 'text', 'usage', 'response_id'}; raises AIError on any
    transport or shape failure without ever including the key.
    """
    from .reviewer import AIError, _request
    model = model_id('meta/' + MODEL_ID)
    if not isinstance(key, str) or not key:
        raise AIError('Add your Meta API key in Vector settings.')
    if images is None:
        if not isinstance(image_b64, str) or not image_b64 or len(image_b64) > 4 * 1024 * 1024:
            raise AIError('Scan image is missing or too large.')
        shots = [{'b64': image_b64, 'mime': mime, 'label': ''}]
    else:
        shots = [{'b64': s.get('b64', ''), 'mime': s.get('mime', ''), 'label': s.get('label', '')}
                 for s in images]
        if not shots or not all(s['b64'] for s in shots):
            raise AIError('Scan images are missing.')
        if any(len(s['b64']) > 4 * 1024 * 1024 for s in shots):
            raise AIError('A scan image is too large.')
        if sum(len(s['b64']) for s in shots) > 24 * 1024 * 1024:
            raise AIError('Too many large photos in one request; split the batch.')
    content = [{'type': 'input_text', 'text': prompt}]
    for i, s in enumerate(shots):
        if s['label']:
            content.append({'type': 'input_text', 'text': f'Photo {i + 1}: {s["label"]} '})
        content.append({'type': 'input_image',
                        'image_url': f'data:{s["mime"]};base64,{s["b64"]}'})
    request = {'model': model,
               'input': [{'type': 'message', 'role': 'user', 'content': content}],
               'max_output_tokens': max_output_tokens,
               'reasoning': {'effort': effort}}
    attempt = {'request_id': uuid.uuid4().hex, 'at': datetime.now(timezone.utc).isoformat(),
               'model': 'meta/' + model, 'action': current_action(), 'status': 'failed',
               'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
               'input_usd_per_m': PRICING_USD_PER_M['input'],
               'output_usd_per_m': PRICING_USD_PER_M['output'],
               'cached_usd_per_m': PRICING_USD_PER_M['cached_input']}

    def finish(status, usage):
        attempt.update(status=status, **usage)
        _record(attempt)

    if progress:
        progress('Reading scan images…' if len(shots) > 1 else 'Reading scan image…')
    try:
        result = _request(RESPONSES_ENDPOINT, request, {'Authorization': f'Bearer {key}'},
                          'meta', timeout=timeout, max_attempts=max_attempts, progress=progress)
    except Exception:
        finish('failed', {})
        raise
    texts = []
    if isinstance(result, dict):
        for item in result.get('output') or []:
            if not isinstance(item, dict):
                continue
            for block in item.get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'output_text':
                    texts.append(block.get('text', ''))
    raw_usage = result.get('usage') if isinstance(result, dict) else None
    raw_usage = raw_usage if isinstance(raw_usage, dict) else {}
    total_in = _count(raw_usage, 'input_tokens')
    total_out = _count(raw_usage, 'output_tokens')
    details = raw_usage.get('input_tokens_details')
    cached = _count(details if isinstance(details, dict) else {}, 'cached_tokens')
    recorded = {'input_tokens': total_in or 0, 'output_tokens': total_out or 0,
                'cached_input_tokens': cached or 0}
    text = '\n'.join(texts).strip()
    if not text:
        finish('incomplete', recorded)
        raise AIError('Meta returned no readable answer for this scan image. Nothing was merged.',
                      diagnostics={'error_kind': 'incomplete', 'retryable': True})
    finish('completed', recorded)
    usage = {}
    if total_in is not None:
        usage['total_input_tokens'] = total_in
    if total_out is not None:
        usage['total_output_tokens'] = total_out
    if total_in is not None and total_out is not None:
        usage['total_tokens'] = total_in + total_out
    if cached is not None:
        usage['cached_input_tokens'] = cached
    response_id = result.get('id') if isinstance(result, dict) else None
    return {'status': 'completed', 'text': text, 'usage': usage,
            'response_id': response_id if isinstance(response_id, str) else ''}


def post_responses_grounded(*, instructions, input_text, key, timeout=120,
                             max_output_tokens=2000, max_attempts=1, progress=None,
                             effort='minimal'):
    """One bounded Meta Responses API call with the web_search grounding tool.

    Uses the Contributor model exclusively. Returns {'status', 'text',
    'citations', 'searched', 'usage'} where citations are provider-issued
    (url, title, offsets into text) and searched is True only when the
    provider's output proves a web search call ran. Raises AIError on any
    transport or shape failure without ever including the key.
    """
    from .reviewer import AIError, _request
    model = model_id('meta/' + MODEL_ID)
    if not isinstance(key, str) or not key:
        raise AIError('Add your Meta API key in Vector settings.')
    if not isinstance(input_text, str) or not input_text.strip():
        raise AIError('Web search needs a question to ground.')
    request = {'model': model, 'store': False,
               'instructions': instructions,
               'input': input_text.strip(),
               'max_output_tokens': max_output_tokens,
               'tools': [{'type': 'web_search'}],
               # NOTE: the provider currently supports only tool_choice auto,
               # so callers that need a guaranteed search must verify the
               # web_search_call marker in the output and retry on a skip.
               'tool_choice': 'auto',
               'reasoning': {'effort': effort}}
    attempt = {'request_id': uuid.uuid4().hex, 'at': datetime.now(timezone.utc).isoformat(),
               'model': 'meta/' + model, 'action': current_action(), 'status': 'failed',
               'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0,
               'input_usd_per_m': PRICING_USD_PER_M['input'],
               'output_usd_per_m': PRICING_USD_PER_M['output'],
               'cached_usd_per_m': PRICING_USD_PER_M['cached_input']}

    def finish(status, usage):
        attempt.update(status=status, **usage)
        _record(attempt)

    if progress:
        progress('Searching the web…')
    try:
        result = _request(RESPONSES_ENDPOINT, request, {'Authorization': f'Bearer {key}'},
                          'meta', timeout=timeout, max_attempts=max_attempts,
                          progress=progress)
    except Exception:
        finish('failed', {})
        raise
    if not isinstance(result, dict):
        finish('incomplete', {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0})
        raise AIError('Meta web search returned an unreadable response. Nothing was searched.')
    if result.get('status') != 'completed':
        # Reasoning tokens share the output budget, so a large reasoning
        # trace can squeeze out the answer. Never present a cut-off draft
        # as a grounded result.
        reason = (result.get('incomplete_details') or {}).get('reason', '') if isinstance(result.get('incomplete_details'), dict) else ''
        finish('incomplete', {'input_tokens': 0, 'output_tokens': 0, 'cached_input_tokens': 0})
        raise AIError('Meta web search stopped before finishing its answer. No web result was accepted; try again without web search for a project-evidence answer.',
                      diagnostics={'error_kind': 'truncated' if reason == 'max_output_tokens' else 'incomplete',
                                   'retryable': False, 'response_status': result.get('status'), 'reason': reason})
    text, citations, searched = _grounded_output(result)
    raw_usage = result.get('usage') or {}
    total_in = _count(raw_usage, 'input_tokens')
    total_out = _count(raw_usage, 'output_tokens')
    details = raw_usage.get('input_tokens_details')
    if not isinstance(details, dict):
        details = raw_usage.get('prompt_tokens_details')
    cached = _count(details if isinstance(details, dict) else {}, 'cached_tokens')
    recorded = {'input_tokens': total_in or 0, 'output_tokens': total_out or 0,
                'cached_input_tokens': cached or 0}
    if not text.strip():
        finish('incomplete', recorded)
        raise AIError('Meta web search returned no readable answer. Nothing was searched.')
    finish('completed', recorded)
    usage = {}
    if total_in is not None:
        usage['total_input_tokens'] = total_in
    if total_out is not None:
        usage['total_output_tokens'] = total_out
    if total_in is not None and total_out is not None:
        usage['total_tokens'] = total_in + total_out
    if cached is not None:
        usage['cached_input_tokens'] = cached
    return {'status': 'completed', 'text': text, 'citations': citations,
            'searched': searched, 'usage': usage}


def _grounded_output(result):
    """Text, provider citations and search proof from a Responses payload.

    Accepts the documented Responses shape (output items with content blocks
    carrying url_citation annotations, plus web_search_call items) and stays
    silent when the provider ran no search, so an ungrounded answer is never
    presented as web-verified.
    """
    from urllib.parse import urlsplit
    text, citations, searched = '', [], False
    for item in result.get('output') or []:
        if not isinstance(item, dict):
            continue
        kind = item.get('type')
        if kind == 'web_search_call':
            searched = True
            continue
        if kind != 'message':
            continue
        for part in item.get('content') or []:
            if not isinstance(part, dict):
                continue
            if part.get('type') == 'refusal':
                from .reviewer import AIError
                raise AIError('Meta declined this web search question.')
            if part.get('type') != 'output_text':
                continue
            value = part.get('text', '')
            if not isinstance(value, str):
                continue
            offset = len(text)
            text += value
            for annotation in part.get('annotations') or []:
                if not isinstance(annotation, dict) or annotation.get('type') != 'url_citation':
                    continue
                url = annotation.get('url', '')
                try:
                    parsed = urlsplit(url)
                except ValueError:
                    continue
                start, end = annotation.get('start_index', annotation.get('start')), \
                    annotation.get('end_index', annotation.get('end'))
                if (parsed.scheme not in ('https', 'http') or not parsed.hostname
                        or parsed.username or parsed.password
                        or type(start) is not int or type(end) is not int
                        or not 0 <= start <= end <= len(value)):
                    continue
                citations.append({'start': offset + start, 'end': offset + end, 'url': url,
                                  'title': str(annotation.get('title') or parsed.hostname)[:300]})
    if text and not searched:
        # No provider search proof: the text is an ordinary model answer.
        return text, [], False
    return text, citations, searched
