"""Exercise transient failures through real callers, with no external API calls."""
import json
import unittest
import socket
import ssl
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from test_design_review import project, design_result
from pcb_checker.ai.reviewer import AIError, DEFAULT_MODEL, _pace_gemini_request, post_ai, _request, evidence_packet, run_ai_review
from pcb_checker.design_review import local_plan
from pcb_checker.step_help import answer_step


def response(value):
    handle = Mock()
    handle.__enter__ = Mock(return_value=handle)
    handle.__exit__ = Mock(return_value=None)
    handle.read.return_value = json.dumps({'choices': [{'message': {'content': json.dumps(value)},
                                                     'finish_reason': 'stop'}],
                                         'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}).encode()
    return handle


def outage(code=503):
    return HTTPError('https://example.com', code, 'temporary', {}, None)


class RetryPathsTests(unittest.TestCase):
    def test_gemini_requests_wait_outside_the_safe_per_minute_input_budget(self):
        import pcb_checker.ai.reviewer as reviewer
        clock=[100.0]
        messages=[]
        def sleep(seconds): clock[0]+=seconds
        with patch.object(reviewer,'_GEMINI_INPUT_WINDOW',[(100.0,reviewer.GEMINI_INPUT_TOKENS_PER_MINUTE)]), \
             patch.object(reviewer.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(reviewer.time,'sleep',side_effect=sleep):
            _pace_gemini_request({'input':'test'},progress=messages.append)
        self.assertGreaterEqual(clock[0],160.0)
        self.assertTrue(any('safe token rate' in message for message in messages))

    def test_meta_service_retries_resubmit_each_prompt(self):
        import pcb_checker.ai.reviewer as reviewer
        clock=[100.0];sent=[]
        def send(*args,**kwargs):
            sent.append(clock[0])
            if len(sent)<3:raise outage()
            return response({})
        with patch.object(reviewer.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(reviewer.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)), \
             patch.object(reviewer,'build_opener') as opener:
            opener.return_value.open.side_effect=send
            result=post_ai({'model': DEFAULT_MODEL, 'input':'x'*180},'test-key',timeout=180)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(sent,[100.0,105.0,120.0])

    def test_pacing_does_not_wait_past_request_deadline(self):
        import pcb_checker.ai.reviewer as reviewer
        clock=[100.0]
        with patch.object(reviewer,'_GEMINI_INPUT_WINDOW',[(100.0,reviewer.GEMINI_INPUT_TOKENS_PER_MINUTE)]), \
             patch.object(reviewer.time,'monotonic',side_effect=lambda:clock[0]), \
             patch.object(reviewer.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)):
            with self.assertRaises(AIError):
                _pace_gemini_request({'input':'test'},deadline=107)
        self.assertEqual(clock[0],107)

    def test_socket_timeout_gets_another_attempt_within_shared_budget(self):
        # Attempt splitting still lives in _request for callers that pass an
        # explicit cap. post_ai no longer forwards caps for Meta (non-streaming
        # generations need the full deadline), so this exercises _request.
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer._pace_gemini_request'), \
                patch('pcb_checker.ai.reviewer.time.monotonic', side_effect=[0,0,0,0,45,47]), \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect=[URLError(TimeoutError('timed out')),response({})]
            result=_request('https://example.com',{},{'Authorization':'Bearer test-key'},
                            'meta',timeout=120,attempt_timeout=45)
        self.assertIn('choices',result)
        self.assertEqual([c.kwargs['timeout'] for c in opener.return_value.open.call_args_list],[45,45])

    def test_timeout_attempts_are_bounded_by_total_budget(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer._pace_gemini_request'), \
                patch('pcb_checker.ai.reviewer.time.monotonic', side_effect=[0,0,0,45,47,112]), \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect=TimeoutError('timed out')
            with self.assertRaises(AIError) as caught:
                _request('https://example.com',{},{'Authorization':'Bearer test-key'},
                         'meta',timeout=120,attempt_timeout=45)
        self.assertEqual([c.kwargs['timeout'] for c in opener.return_value.open.call_args_list],[45,45,8])
        self.assertEqual(caught.exception.diagnostics['error_kind'],'timeout')
        self.assertEqual(caught.exception.diagnostics['attempts'],3)

    def test_reset_recovers_but_certificate_and_permanent_dns_fail_once(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect=[ConnectionResetError('reset'),response({})]
            self.assertEqual(post_ai({'model': DEFAULT_MODEL},'test-key')['status'],'completed')
        for reason,kind in [(ssl.SSLCertVerificationError('bad certificate'),'tls'),
                            (socket.gaierror(socket.EAI_NONAME,'host missing'),'dns')]:
            with self.subTest(kind=kind), patch('pcb_checker.ai.reviewer.build_opener') as opener:
                opener.return_value.open.side_effect=URLError(reason)
                with self.assertRaises(AIError) as caught:post_ai({'model': DEFAULT_MODEL},'test-key')
                opener.return_value.open.assert_called_once()
                self.assertEqual(caught.exception.diagnostics['error_kind'],kind)
                self.assertFalse(caught.exception.diagnostics['retryable'])

    def test_connection_diagnostics_redact_key_and_preserve_prior_503(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect=[outage(),TimeoutError('test-key'),TimeoutError('test-key')]
            with self.assertRaises(AIError) as caught:post_ai({'model': DEFAULT_MODEL},'test-key')
        self.assertEqual(caught.exception.diagnostics['previous_http_status'],503)
        self.assertNotIn('test-key',json.dumps(caught.exception.diagnostics))

    def test_design_review_recovers_and_announces_retry(self):
        p = project()
        progress = Mock()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [outage(), response(design_result(evidence_packet(p)))]
            result = run_ai_review(p, key='test-key', design=True, progress=progress)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertTrue(any('attempt 2/3' in c.args[0] for c in progress.call_args_list))

    def test_step_question_recovers(self):
        p = project()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [outage(), response({'answer': 'Inspect the marked pad.'})]
            result = answer_step(p, p.review['groups'][0], local_plan(p)['actions'][0],
                                 0, 'Explain this step', [], key='test-key', model=DEFAULT_MODEL)
        self.assertEqual(result['status'], 'ai')
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_step_outage_is_reported_as_retryable_failure(self):
        p = project()
        with patch('pcb_checker.step_help.post_ai', side_effect=AIError(
                'HTTP 503 after 3 attempts', diagnostics={'http_status': 503})):
            result = answer_step(p, p.review['groups'][0], local_plan(p)['actions'][0],
                                 0, 'Explain this step', [], key='test-key', model=DEFAULT_MODEL)
        self.assertEqual(result['status'], 'failed')
        self.assertTrue(result['retryable'])
        self.assertIn('not answered', result['message'])
        self.assertIn('503', result['detail'])
        self.assertNotIn('answer', result)

    def test_retry_uses_remaining_timeout(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer._pace_gemini_request'), \
                patch('pcb_checker.ai.reviewer.time.monotonic', side_effect=[0, 0, 0, 12]), \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [outage(), response({})]
            post_ai({'model': DEFAULT_MODEL}, 'test-key', timeout=45)
        self.assertEqual([c.kwargs['timeout'] for c in opener.return_value.open.call_args_list], [45, 33])

    def test_no_retry_when_budget_exhausted(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer._pace_gemini_request'), \
                patch('pcb_checker.ai.reviewer.time.monotonic', side_effect=[0, 0, 40]), \
                patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = outage()
            with self.assertRaises(AIError) as caught:
                post_ai({'model': DEFAULT_MODEL}, 'test-key', timeout=45)
        self.assertEqual(caught.exception.diagnostics['attempts'], 1)
        opener.return_value.open.assert_called_once()
        sleep.assert_not_called()

    def test_design_socket_timeout_fires_second_attempt_within_total(self):
        # Structural regression: a fast-failing first attempt must retry
        # within the shared budget instead of reporting "Request failed after
        # 1 attempt(s)". Meta attempts carry the full deadline (non-streaming
        # generations send no bytes until completion, so splitting the window
        # kills every slow generation mid-flight); fast failures return at
        # once and keep their retry headroom.
        import pcb_checker.ai.reviewer as reviewer
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [TimeoutError('timed out'), response({})]
            result = post_ai({'model': DEFAULT_MODEL}, 'test-key', max_attempts=3,
                             timeout=180, attempt_timeout=90)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 2)
        timeouts = [c.kwargs['timeout'] for c in opener.return_value.open.call_args_list]
        self.assertTrue(all(t > 179 for t in timeouts))

    def test_permanent_errors_are_not_retried(self):
        for code in (400, 401, 403, 404, 429):
            with self.subTest(code=code), patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                    patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
                opener.return_value.open.side_effect = outage(code)
                with self.assertRaises(AIError):
                    post_ai({'model': DEFAULT_MODEL}, 'test-key')
                opener.return_value.open.assert_called_once()
                sleep.assert_not_called()

    def test_cancellation_during_backoff_stops_retries(self):
        class Cancelled(Exception):
            pass
        def backoff_cancel(message):
            if 'attempt 2' in message:
                raise Cancelled()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = outage()
            with self.assertRaises(Cancelled):
                post_ai({'model': DEFAULT_MODEL}, 'test-key', progress=backoff_cancel)
        opener.return_value.open.assert_called_once()
        sleep.assert_not_called()
