"""Responses streaming must never promote a partial generation to a valid plan."""
import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import _read_openai_stream, AIError, post_ai, RoutedKey, _object, STR


def payload():
    return {'model': 'gpt-6-luna', 'store': False, 'input': 'Preserve the supplied board evidence.',
            'system_instruction': 'Explain the selected issue.',
            'generation_config': {'max_output_tokens': 1400, 'thinking_level': 'low'},
            'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': _object({'answer': STR})}}


def response(value):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15, 'output_tokens_details': {'reasoning_tokens': 2}}}


def stream(*events):
    return io.BytesIO(b''.join(b'event: ignored\ndata: ' + json.dumps(event).encode() + b'\n\n' for event in events))


class OpenAIStreamTests(unittest.TestCase):
    def test_completion_preserves_usage_and_ignores_partial_text(self):
        final = response({'answer': 'Ground plane'})
        messages = []
        value = _read_openai_stream(stream(
            {'type': 'response.created'},
            {'type': 'response.output_text.delta', 'delta': 'unfinished'},
            {'type': 'response.completed', 'response': final}),
            deadline=time.monotonic()+60, progress=messages.append)
        self.assertEqual(value, final)
        self.assertEqual(len(messages), 1)
        self.assertNotIn('unfinished', ''.join(messages))

    def test_disconnect_and_done_without_completion_fail(self):
        for source in [stream({'type': 'response.output_text.delta', 'delta': '{}'}), io.BytesIO(b'data: [DONE]\n\n')]:
            with self.assertRaises(AIError):
                _read_openai_stream(source, deadline=time.monotonic()+60)

    def test_incomplete_remains_incomplete(self):
        result = _read_openai_stream(stream({'type':'response.incomplete', 'response':{'status':'incomplete'}}), deadline=time.monotonic()+60)
        self.assertEqual(result['status'], 'incomplete')

    def test_late_final_event_cannot_pass_after_blocking_read(self):
        clock=[10.]
        class LateStream(io.BytesIO):
            def readline(self,*args):
                value=super().readline(*args)
                if value==b'\n':clock[0]=100.
                return value
        raw=stream({'type':'response.completed','response':response({'answer':'too late'})}).getvalue()
        with patch('pcb_checker.ai.reviewer.time.monotonic',side_effect=lambda:clock[0]):
            with self.assertRaisesRegex(AIError,'time budget'):
                _read_openai_stream(LateStream(raw),deadline=20.)

    def test_elapsed_wall_time_invalidates_response_after_system_pause(self):
        with self.assertRaisesRegex(AIError,'time budget'):
            _read_openai_stream(stream({'type':'response.completed','response':response({'answer':'late'})}),
                deadline=time.monotonic()+60,wall_deadline=time.time()-1)

    def test_stalled_socket_is_interrupted_without_waiting_for_more_bytes(self):
        import threading
        from types import SimpleNamespace
        stopped=threading.Event()
        sock=SimpleNamespace(shutdown=lambda _:stopped.set())
        class Stalled:
            fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock))
            def readline(self,*args):
                if not stopped.wait(1):raise AssertionError('Deadline did not interrupt the stream')
                return b''
        started=time.monotonic()
        with self.assertRaisesRegex(AIError,'time budget'):
            _read_openai_stream(Stalled(),deadline=started+.03)
        self.assertLess(time.monotonic()-started,1)

    def test_stream_error_redacts_secret(self):
        with self.assertRaises(AIError) as error:
            _read_openai_stream(stream({'type':'error', 'code':'server_error', 'message':'Oops sk-secret'}), deadline=time.monotonic()+60, secrets=['sk-secret'])
        self.assertNotIn('sk-secret', str(error.exception))
        self.assertNotIn('sk-secret', json.dumps(error.exception.diagnostics))

    def test_deadline_and_cancellation(self):
        with self.assertRaisesRegex(AIError, "time budget"):
            _read_openai_stream(io.BytesIO(), deadline=time.monotonic()-1)
        def cancel(message): raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            _read_openai_stream(stream({'type':'response.created'}), deadline=time.monotonic()+60, progress=cancel)

    def test_backup_keys_are_never_consulted(self):
        key = RoutedKey('sk-test', backup='gemini-secret')
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            with self.assertRaisesRegex(AIError, 'exclusively'):
                post_ai(payload(), key, timeout=180)
        opener.assert_not_called()
        self.assertFalse(key.providers_used)
        self.assertFalse(key.provider_failures)

if __name__=='__main__':unittest.main()
