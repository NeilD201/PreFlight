"""Diagnose API failures without reading PCB files or leaking credentials."""

from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.ai.reviewer import AIError, DEFAULT_MODEL, MAX_ERROR_BYTES, post_ai, check_ai_connection, run_ai_review
from pcb_checker.__main__ import main
from pcb_checker.report import html_report
from pcb_checker.review import build_review
from test_ai_review import meta_api_response, meta_response
from test_review import fixture


TEST_RESULT = {'summary': 'API connection test received.', 'answer': '', 'findings': [],
               'questions': [], 'limitations': []}


def http_failure(code, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HTTPError('https://api.meta.ai/v1/chat/completions',
                     code, 'Untrusted HTTP reason is not displayed', {}, io.BytesIO(raw))


@patch.dict('os.environ', {}, clear=True)
class DiagnosticTests(unittest.TestCase):
    def test_meta_error_is_bounded_redacted_and_kept_in_report(self):
        secret = 'fake-key+with/slash'
        body = {'error': {'code': 'service_unavailable',
                'message': f'Model unavailable. Key {secret} / {quote(secret, safe="")} '
                           '\x1b[31m <script>alert(1)</script>',
                'details': {'unrelated_secret': 'do-not-display-this'}}}
        failure = http_failure(503, body)
        project = fixture()
        project.review = build_review(project)
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            opener.return_value.open.side_effect = failure
            result = run_ai_review(project, key=secret, max_attempts=1)
        self.assertTrue(failure.fp.closed)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['diagnostics']['http_status'], 503)
        self.assertEqual(result['diagnostics']['attempts'], 1)
        self.assertEqual(result['diagnostics']['service_code'], 'service_unavailable')
        self.assertIn('Model unavailable', result['message'])
        self.assertIn('[redacted]', result['message'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(quote(secret, safe=''), json.dumps(result))
        self.assertNotIn('do-not-display-this', json.dumps(result))
        self.assertNotIn('\x1b', result['message'])
        project.ai_review = result
        html = html_report(project)
        self.assertNotIn('<script>alert(1)', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('--test-ai', html)

    def test_standard_meta_error_status_is_supported(self):
        error = http_failure(400, {'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
                              'message': 'Unsupported request field.'}})
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(AIError) as caught:
                post_ai({'model': DEFAULT_MODEL}, 'fake-key')
        self.assertEqual(caught.exception.diagnostics['service_code'], 'INVALID_ARGUMENT')
        self.assertIn('Unsupported request field', str(caught.exception))
        self.assertIn('request format', str(caught.exception))
        self.assertNotIn('Check LLAMA_API_KEY', str(caught.exception))
        opener.return_value.open.assert_called_once()

    def test_explicit_invalid_key_in_400_is_still_identified(self):
        error = http_failure(400, {'error': {'status': 'INVALID_ARGUMENT',
                              'message': 'API key not valid. Please pass a valid API key.'}})
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(AIError) as caught:
                post_ai({'model': DEFAULT_MODEL}, 'fake-key')
        self.assertIn('API key was rejected', str(caught.exception))
        opener.return_value.open.assert_called_once()

    def test_unreadable_and_oversized_error_bodies_are_omitted(self):
        bodies = [b'<html>secret-html</html>', b'x' * (MAX_ERROR_BYTES + 1),
                  b'[]', b'{"error": "invalid"}', b'null', b'\xff']
        for body in bodies:
            with self.subTest(body_length=len(body)), patch('pcb_checker.ai.reviewer.build_opener') as opener:
                error = http_failure(503, body)
                opener.return_value.open.side_effect = error
                with self.assertRaises(AIError) as caught:
                    post_ai({'model': DEFAULT_MODEL}, 'fake-key', max_attempts=1)
                self.assertEqual(caught.exception.diagnostics['http_status'], 503)
                self.assertNotIn('service_message', caught.exception.diagnostics)
                self.assertNotIn('secret-html', str(caught.exception))
                self.assertTrue(error.fp.closed)

    def test_diagnostic_reuses_review_schema_with_no_board_data_and_bounded_retries(self):
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=meta_response(TEST_RESULT)) as post:
            result = check_ai_connection(key='fake-key', model=DEFAULT_MODEL)
        payload = post.call_args.args[0]
        evidence = json.loads(payload['input'])
        self.assertEqual(evidence['components'], [])
        self.assertEqual(evidence['nets'], [])
        self.assertEqual(evidence['review_groups'], [])
        self.assertNotIn('archive', evidence)
        self.assertEqual(payload['model'], DEFAULT_MODEL)
        self.assertFalse(payload['store'])
        self.assertEqual(post.call_args.kwargs, {'max_attempts': 1, 'timeout': 60, 'attempt_timeout': 90})
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['test'], 'small_structured_request')
        self.assertNotIn('result', result)
        self.assertIn('full PCB review still needs to run', result['message'])

    def test_diagnostic_503_keeps_provider_reason_without_hidden_retries(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = [http_failure(503, {
                'error': {'code': 'service_unavailable', 'message': 'High demand for this model.'}})]
            result = check_ai_connection(key='fake-key')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('High demand', result['message'])
        self.assertEqual(result['diagnostics']['attempts'], 1)
        # Single complete tier specifies a connection allowance of 2
        # (ai/usage.py LIMITS); the removed economy/detailed tiers used 1.
        self.assertEqual(result['request_usage']['request_limit'], 2)
        self.assertEqual(opener.return_value.open.call_count, 1)
        sleep.assert_not_called()

    def test_invalid_test_reply_does_not_confirm_success(self):
        for response in ({'status': 'incomplete'}, meta_response()):
            # The latter cites board evidence absent from the connection test.
            with self.subTest(response=response), patch('pcb_checker.ai.reviewer.post_ai', return_value=response):
                result = check_ai_connection(key='fake-key')
                self.assertEqual(result['status'], 'failed')

    def test_project_id_and_invalid_key_characters_are_rejected_locally(self):
        for key in ('gen-lang-client-0695817822', 'key with spaces', 'key\nheader: value', 'key\x1b'):
            with self.subTest(key=repr(key)), patch('pcb_checker.ai.reviewer.post_ai') as post:
                result = check_ai_connection(key=key)
                self.assertEqual(result['status'], 'not_configured')
                self.assertNotIn(key, json.dumps(result))
                post.assert_not_called()

    def test_cli_connection_test_skips_prompt_kicad_and_report_files(self):
        with patch.dict('os.environ', {'LLAMA_API_KEY': 'fake-key'}), \
             patch('pcb_checker.__main__.prompt_project_folder') as prompt, \
             patch('pcb_checker.__main__.analyze_project') as analyze, \
             patch('pcb_checker.__main__.save_report') as save, \
             patch('pcb_checker.ai.reviewer.post_ai', return_value=meta_response(TEST_RESULT)), \
             patch('sys.argv', ['main.py', '--test-ai', '--json', '--ai-model', DEFAULT_MODEL]), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result['model'], DEFAULT_MODEL)
        self.assertEqual(result['status'], 'completed')
        prompt.assert_not_called()
        analyze.assert_not_called()
        save.assert_not_called()

    def test_cli_test_without_key_does_not_send_a_request(self):
        with patch('pcb_checker.local_env.load_local_env', lambda: None), \
             patch('sys.stdin.isatty', return_value=False), \
             patch('pcb_checker.ai.reviewer.post_ai') as post, \
             patch('sys.argv', ['main.py', '--test-ai', '--json']), \
             redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()):
            self.assertEqual(main(), 3)
        self.assertEqual(json.loads(out.getvalue())['status'], 'not_configured')
        post.assert_not_called()

    def test_meta_503_message_identifies_the_actual_provider_and_attempts(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            opener.return_value.open.side_effect = http_failure(503, {})
            result = check_ai_connection(key='fake-key', provider='meta')
        self.assertIn('Meta returned HTTP 503', result['message'])
        self.assertEqual(result['diagnostics']['attempts'], 1)
        self.assertNotIn('OpenAI', result['message'])


if __name__ == '__main__':
    unittest.main()
