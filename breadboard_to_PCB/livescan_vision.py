"""LiveScan image interpretation through Meta Muse Spark 1.3 Contributor.

One bounded Responses API call per photo: the model returns strict JSON
describing what it actually sees (components, wires, board, questions,
next-view guidance). Everything the model says is sanitized here and merged
by the client as *inferred* evidence only — never confirmed, never exact
hole placements (the photo has no board registration).
"""
import base64
import json
import re
import threading
import time

from pcb_checker.ai.meta import meta_action, post_responses_image
from pcb_checker.ai.reviewer import AIError

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MIME_BY_SUFFIX = {'.png': 'image/png', '.jpg': 'image/jpeg',
                  '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
MAX_COMPONENTS = 40
MAX_WIRES = 40
MAX_QUESTIONS = 3


def _fit_for_vision(image_bytes, suffix):
    """Shrink oversized photos for the vision call.

    Real phone photos (2-8 MB) must not hard-fail interpretation: the stored
    original is untouched (display/registration keep full resolution) while
    the model receives a bounded JPEG. EXIF orientation is applied so the
    model sees the photo upright.
    """
    if len(image_bytes) <= MAX_IMAGE_BYTES:
        return image_bytes, suffix
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise AIError('Scan image is larger than 2 MB; capture a smaller frame.')
    import io
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
        img.load()
    except Exception:
        raise AIError('Scan image could not be decoded.')
    if img.mode != 'RGB':
        img = img.convert('RGB')
    for maxdim, quality in ((1600, 85), (1280, 80), (1024, 75)):
        w, h = img.size
        scale = min(1.0, maxdim / max(w, h))
        frame = img.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                           Image.LANCZOS) if scale < 1 else img
        buf = io.BytesIO()
        frame.save(buf, 'JPEG', quality=quality)
        if buf.tell() <= MAX_IMAGE_BYTES:
            return buf.getvalue(), '.jpg'
    raise AIError('Scan image is larger than 2 MB even after shrinking; capture a smaller frame.')

# Component kinds the 3D twin can draw. Anything else becomes 'generic'.
KNOWN_KINDS = {'resistor', 'capacitor', 'led', 'diode', 'dip8', 'dip14',
               'dip16', 'esp32devkit', 'sensor3', 'button', 'generic'}

_lock = threading.Lock()
_in_flight = set()      # project ids with a running interpret call
_last_call = {}         # project id -> monotonic timestamp of last call
_calls_window = {}      # project id -> [window start, count]
COOLDOWN_S = 15
WINDOW_S = 3600
WINDOW_MAX = 30

SYSTEM_PROMPT = """You are PreFlight LiveScan, an electronics engineering vision assistant.
You are shown ONE photo of a solderless breadboard circuit. Report ONLY what you
can actually see. Never invent markings, values, part numbers, or connections
you cannot see. If a detail is unreadable, say so.

The breadboard has terminal strips (columns of connected holes), power rails
along the edges (usually marked red + and blue -), and a center groove.
Holes are too small to count exactly from most photos: describe positions with
normalized bounding boxes instead of hole names. CLOSE-UP or REGISTERED views
are the exception: where a wire end is clearly visible entering one hole, name
it in "hole_ends" as t<row><A-J> (e.g. t12C) or r<L|R><+|-><pos> (e.g. rL+5),
'' when not clearly visible. Never guess a hole ID. Row numbers are printed
along the board: read them as printed. Only if you visibly see the same number
twice on one board, state which markers you used (e.g. "left of the 25 mark").

List EVERY component and wire visible in THIS photo, including ones named in
'Already modeled' — repeat sightings with this photo's boxes are wanted, so
never omit a visible part just because another view saw it. One entry per
physical wire: a wire crossing the frame in segments is still ONE entry
spanning its full visible extent. Colors must be basic English only: black,
white, red, orange, yellow, green, blue, purple, brown, gray — pick the
closest. For endpoints describe EACH end with visible landmarks (e.g.
'off-board connector at left edge', 'top power rail near right end'); say
explicitly when an end is hidden, off-frame, or off-board.

Respond with JSON ONLY, exactly this shape:
{"board": {"size_guess": "full|half|unknown",
           "rails_visible": ["left"|"right"],
           "coverage_notes": "<what areas are unclear, cut off, or blurry>"},
 "components": [{"kind": "<resistor|capacitor|led|diode|dip8|dip14|dip16|esp32devkit|sensor3|button|generic>",
                 "label": "<short visible description, e.g. 'blue resistor, bands unreadable'>",
                 "value_guess": "<exact value ONLY if clearly readable, else ''>",
                 "bbox": [x0, y0, x1, y1],
                 "confidence": "<high|medium|low>",
                 "notes": "<markings, orientation, anything unclear>"}],
 "wires": [{"color": "<color>", "bbox": [x0, y0, x1, y1],
            "endpoints": "<where each end seems to go, or 'unclear'>",
            "hole_ends": ["<hole ID e.g. t12C or rL+5, '' when not clearly visible>", "<same for other end>"],
            "confidence": "<high|medium|low>"}],
 "questions": ["<max 3 short questions for the builder about things ONLY they can resolve>"],
 "next_view": "<one short instruction for the next photo, e.g. 'closer view of the upper-right IC markings'>"}
Bounding boxes are fractions 0..1 of image width/height (x0<x1, y0<y1).
List every distinct component and wire you can see. No other text."""


def _budget_check(project_id, manual):
    now = time.monotonic()
    with _lock:
        if project_id in _in_flight:
            raise AIError('An interpretation for this project is already running.',
                          diagnostics={'error_kind': 'busy', 'retryable': True})
        if not manual:
            last = _last_call.get(project_id, 0)
            if now - last < COOLDOWN_S:
                raise AIError('Scan interpretation is cooling down; capture the next view first.',
                              diagnostics={'error_kind': 'cooldown', 'retryable': True})
        start, count = _calls_window.get(project_id, (now, 0))
        if now - start > WINDOW_S:
            start, count = now, 0
        if count >= WINDOW_MAX:
            raise AIError('This project reached 30 scan interpretations per hour.',
                          diagnostics={'error_kind': 'request_budget', 'retryable': False})
        _calls_window[project_id] = (start, count + 1)
        _last_call[project_id] = now
        _in_flight.add(project_id)


def _budget_release(project_id):
    with _lock:
        _in_flight.discard(project_id)


def _clean_str(value, limit=300):
    if not isinstance(value, str):
        return ''
    return value.strip()[:limit]


def _clean_bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        nums = [max(0.0, min(1.0, float(v))) for v in value]
    except (TypeError, ValueError):
        return None
    if nums[0] >= nums[2] or nums[1] >= nums[3]:
        return None
    if nums[0] <= 0.01 and nums[1] <= 0.01 and nums[2] >= 0.99 and nums[3] >= 0.99:
        return None  # full-frame box locates nothing; treat as unlocated
    return [round(v, 3) for v in nums]


def _clean_conf(value):
    return value if value in ('high', 'medium', 'low') else 'low'


_HOLE_END_RE = re.compile(r'^(?:[Tt]\d{1,2}[A-Ja-j]|[Rr][LlRr][+-]0*\d{1,2})$')


def _clean_hole_end(value):
    """Strict hole-ID grammar + lattice range; anything else is '' (never guess)."""
    s = _clean_str(value, 12).replace(' ', '')
    if not _HOLE_END_RE.match(s):
        return ''
    if s[0] in 'tT':
        row = int(s[1:-1])
        if not 1 <= row <= 63:
            return ''
        return 't' + str(row) + s[-1].upper()
    num = int(s[3:])
    if not 1 <= num <= 63:
        return ''
    return 'r' + s[1].upper() + s[2] + str(num)


def _clean_hole_ends(value):
    out = ['', '']
    if isinstance(value, list):
        for i in range(min(2, len(value))):
            out[i] = _clean_hole_end(value[i])
    return out


def _balanced_span(text):
    """Largest balanced {...} span, so leading/trailing prose is tolerated."""
    start = text.find('{')
    if start < 0:
        return ''
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return ''


def _clean_photo(value, known_photos):
    name = _clean_str(value, 120)
    if known_photos is not None and name not in known_photos:
        return ''
    return name


def _clean_seen_in(value, known_photos):
    out = []
    if isinstance(value, list):
        for v in value[:8]:
            name = _clean_str(v, 120)
            if name and (known_photos is None or name in known_photos) and name not in out:
                out.append(name)
    return out


def sanitize(text, max_questions=MAX_QUESTIONS, known_photos=None):
    """Validate model JSON into a safe observations dict. Raises AIError."""
    data = None
    if isinstance(text, str):
        candidates = [text]
        if '```' in text:
            inner = text.split('```', 2)
            fenced = inner[1] if len(inner) > 1 else ''
            candidates.append(fenced.split('\n', 1)[1] if '\n' in fenced else fenced)
        span = _balanced_span(text)
        if span and span != text.strip():
            candidates.append(span)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                data = parsed
                break
    if not isinstance(data, dict):
        raise AIError('Meta did not return usable scan JSON. Nothing was merged.',
                      diagnostics={'error_kind': 'unparseable', 'retryable': True})
    board = data.get('board') if isinstance(data.get('board'), dict) else {}
    size = board.get('size_guess')
    rails = board.get('rails_visible')
    out = {'board': {
        'size_guess': size if size in ('full', 'half') else 'unknown',
        'rails_visible': [r for r in rails] if isinstance(rails, list) else [],
        'coverage_notes': _clean_str(board.get('coverage_notes'))},
        'components': [], 'wires': [], 'questions': [],
        'next_view': _clean_str(data.get('next_view'), 200)}
    out['board']['rails_visible'] = [r for r in out['board']['rails_visible']
                                     if r in ('left', 'right')][:2]
    comps = data.get('components')
    if isinstance(comps, list):
        for raw in comps[:MAX_COMPONENTS]:
            if not isinstance(raw, dict):
                continue
            kind = raw.get('kind')
            out['components'].append({
                'kind': kind if kind in KNOWN_KINDS else 'generic',
                'label': _clean_str(raw.get('label'), 120),
                'value_guess': _clean_str(raw.get('value_guess'), 60),
                'bbox': _clean_bbox(raw.get('bbox')),
                'confidence': _clean_conf(raw.get('confidence')),
                'notes': _clean_str(raw.get('notes')),
                'photo': _clean_photo(raw.get('photo'), known_photos),
                'seen_in': _clean_seen_in(raw.get('seen_in'), known_photos)})
    wires = data.get('wires')
    if isinstance(wires, list):
        for raw in wires[:MAX_WIRES]:
            if not isinstance(raw, dict):
                continue
            out['wires'].append({
                'color': _clean_str(raw.get('color'), 30) or 'unknown',
                'bbox': _clean_bbox(raw.get('bbox')),
                'endpoints': _clean_str(raw.get('endpoints')),
                'hole_ends': _clean_hole_ends(raw.get('hole_ends')),
                'confidence': _clean_conf(raw.get('confidence')),
                'photo': _clean_photo(raw.get('photo'), known_photos),
                'seen_in': _clean_seen_in(raw.get('seen_in'), known_photos)})
    questions = data.get('questions')
    if isinstance(questions, list):
        for raw in questions[:max_questions]:
            text_q = _clean_str(raw, 200)
            if text_q:
                out['questions'].append(text_q)
    return out


def interpret(*, project_id, image_bytes, suffix, key, view_hint='', known='',
              manual=False, timeout=120):
    """Run one bounded interpretation. Returns {'observations', 'usage'}."""
    if not key:
        raise AIError('Add your Meta API key in Vector settings.')
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise AIError('Scan image is missing.')
    image_bytes, suffix = _fit_for_vision(image_bytes, suffix)
    mime = MIME_BY_SUFFIX.get((suffix or '').lower())
    if mime is None:
        raise AIError('Scan image must be PNG, JPEG or WebP.')
    _budget_check(project_id, manual)
    try:
        prompt = SYSTEM_PROMPT
        context_bits = []
        if view_hint:
            context_bits.append('View hint: ' + str(view_hint)[:200])
        if known:
            context_bits.append('Already modeled in other views (report again '
                                'if visible here, with this photo\'s boxes; '
                                'only genuinely new parts expand the model): '
                                + str(known)[:800])
        if context_bits:
            prompt += '\n\n' + '\n'.join(context_bits)
        with meta_action('livescan'):
            result = post_responses_image(
                image_b64=base64.b64encode(image_bytes).decode('ascii'),
                mime=mime, prompt=prompt, key=key, timeout=timeout,
                # Reasoning shares this budget; a full board listing needs room.
                max_output_tokens=2500, effort='minimal')
    finally:
        _budget_release(project_id)
    return {'observations': sanitize(result['text']), 'usage': result['usage'],
            'response_id': result.get('response_id', '')}


BATCH_SYSTEM_PROMPT = """You are PreFlight LiveScan, an electronics engineering vision assistant.
You are shown SEVERAL photos labeled Photo 1, Photo 2, ... Every photo shows
the SAME physical solderless breadboard circuit from a different angle or
distance. Build ONE structured reconstruction of that single circuit.

Report ONLY what you can actually see. Never invent markings, values, part
numbers, or connections you cannot see. If a detail is unreadable, say so.

The breadboard has terminal strips (columns of connected holes), power rails
along the edges (usually marked red + and blue -), and a center groove.
Holes are too small to count exactly from most photos: describe positions with
normalized bounding boxes instead of hole names. CLOSE-UP or REGISTERED views
are the exception: where a wire end is clearly visible entering one hole, name
it in "hole_ends" as t<row><A-J> (e.g. t12C) or r<L|R><+|-><pos> (e.g. rL+5),
'' when not clearly visible. Never guess a hole ID. Row numbers are printed
along the board: read them as printed. Only if you visibly see the same number
twice on one board, state which markers you used (e.g. "left of the 25 mark").

Deduplicate across views: the same physical component or wire seen in two
photos is ONE entry. For each component and wire record "photo" (the exact
filename where it is clearest) and "seen_in" (every filename where it is
visible). Bounding boxes are fractions 0..1 of the "photo" image's
width/height (x0<x1, y0<y1). One entry per physical wire: a wire crossing the
frame in segments is still ONE entry spanning its full visible extent. Colors
must be basic English only: black, white, red, orange, yellow, green, blue,
purple, brown, gray — pick the closest. For endpoints describe EACH end with
visible landmarks (e.g. 'off-board connector at left edge', 'top power rail
near right end'); say explicitly when an end is hidden, off-frame, or
off-board.

Respond with JSON ONLY, exactly this shape:
{"board": {"size_guess": "full|half|unknown",
           "rails_visible": ["left"|"right"],
           "coverage_notes": "<what areas are unclear, cut off, or blurry>"},
 "components": [{"kind": "<resistor|capacitor|led|diode|dip8|dip14|dip16|esp32devkit|sensor3|button|generic>",
                 "label": "<short visible description, e.g. 'blue resistor, bands unreadable'>",
                 "value_guess": "<exact value ONLY if clearly readable, else ''>",
                 "bbox": [x0, y0, x1, y1],
                 "confidence": "<high|medium|low>",
                 "notes": "<markings, orientation, anything unclear>",
                 "photo": "<exact filename, clearest view>",
                 "seen_in": ["<every filename where visible>"]}],
 "wires": [{"color": "<color>", "bbox": [x0, y0, x1, y1],
            "endpoints": "<where each end seems to go, or 'unclear'>",
            "hole_ends": ["<hole ID e.g. t12C or rL+5, '' when not clearly visible>", "<same for other end>"],
            "confidence": "<high|medium|low>",
            "photo": "<exact filename, clearest view>",
            "seen_in": ["<every filename where visible>"]}],
 "questions": ["<short questions for the builder about things ONLY they can resolve>"],
 "next_view": "<one short instruction for an additional photo, or '' if coverage is complete>"}
List every distinct component and wire. No other text."""

BATCH_MAX_PHOTOS = 8
# Fitted bytes per request; split above this. Photos at/below 2 MB pass
# through at full resolution, so 7-8 real phone photos total ~8 MB; the
# transport cap is 24 MB of base64 (~18 MB raw), leaving wide headroom.
BATCH_MAX_BYTES = 12 * 1024 * 1024
BATCH_MAX_QUESTIONS = 8


def interpret_batch(*, project_id, photos, key, timeout=240, known=''):
    """Analyze 1-8 photos of one board in the fewest safe requests.

    `photos` is a list of {'name', 'bytes', 'suffix', 'view_hint'}.
    Returns {'batches': [{'photos': [names], 'observations': {...}}],
    'usage': {...totals, 'requests': N, 'cost_usd': X}, 'errors': [{'photo', 'error'}]}.
    A batch is user-initiated, so the camera cooldown never applies; the
    hourly request budget still does. One bad photo never discards the rest.
    """
    if not key:
        raise AIError('Add your Meta API key in Vector settings.')
    photos = [p for p in (photos or []) if isinstance(p, dict) and p.get('name')][:BATCH_MAX_PHOTOS]
    if not photos:
        raise AIError('No photos to analyze.')
    from pcb_checker.ai.meta import estimate_cost_usd
    fitted, errors = [], []
    for p in photos:
        name = str(p.get('name'))
        try:
            body, suffix = _fit_for_vision(p.get('bytes') or b'', p.get('suffix') or '')
        except AIError as exc:
            errors.append({'photo': name, 'error': str(exc)})
            continue
        mime = MIME_BY_SUFFIX.get((suffix or '').lower())
        if mime is None:
            errors.append({'photo': name, 'error': 'Photo must be PNG, JPEG or WebP.'})
            continue
        fitted.append({'name': name, 'body': body, 'mime': mime,
                       'view_hint': str(p.get('view_hint') or ''),
                       'w': p.get('w') or 0, 'h': p.get('h') or 0})
    if not fitted:
        raise AIError(errors[0]['error'] if errors else 'No photos to analyze.')
    # Fewest safe batches by fitted byte size.
    groups, cur, cur_bytes = [], [], 0
    for f in fitted:
        if cur and cur_bytes + len(f['body']) > BATCH_MAX_BYTES:
            groups.append(cur)
            cur, cur_bytes = [], 0
        cur.append(f)
        cur_bytes += len(f['body'])
    if cur:
        groups.append(cur)
    batches, tot_in, tot_out, tot_cached = [], 0, 0, 0
    for group in groups:
        names = [f['name'] for f in group]
        prompt = BATCH_SYSTEM_PROMPT + '\n\nPhotos in this request:\n' + '\n'.join(
            f'- {f["name"]}'
            + (f' ({f["w"]}x{f["h"]})' if f['w'] and f['h'] else '')
            + (f' — {f["view_hint"][:120]}' if f['view_hint'] else '')
            for f in group)
        if known:
            prompt += ('\n\nAlready established in this project (do NOT contradict: '
                       'repeat visible items with this batch\'s boxes, never invent '
                       'against it): ' + str(known)[:1500])
        _budget_check(project_id, True)  # user-initiated: no camera cooldown
        try:
            with meta_action('livescan-batch'):
                result = post_responses_image(
                    image_b64='', mime='image/jpeg', prompt=prompt, key=key,
                    timeout=timeout, max_output_tokens=6000, effort='minimal',
                    images=[{'b64': base64.b64encode(f['body']).decode('ascii'),
                             'mime': f['mime'], 'label': f['name']} for f in group])
        finally:
            _budget_release(project_id)
        obs = sanitize(result['text'], max_questions=BATCH_MAX_QUESTIONS,
                       known_photos=set(names))
        batches.append({'photos': names, 'observations': obs,
                        'response_id': result.get('response_id', '')})
        u = result.get('usage') or {}
        tot_in += u.get('total_input_tokens', u.get('input_tokens')) or 0
        tot_out += u.get('total_output_tokens', u.get('output_tokens')) or 0
        tot_cached += u.get('cached_input_tokens') or 0
    usage = {'input_tokens': tot_in, 'output_tokens': tot_out,
             'cached_input_tokens': tot_cached,
             'total_tokens': tot_in + tot_out, 'requests': len(groups),
             'cost_usd': estimate_cost_usd(tot_in, tot_out, tot_cached)}
    return {'batches': batches, 'usage': usage, 'errors': errors}
