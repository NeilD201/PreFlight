"""Non-Meta providers are rejected before any network use; credential storage is unchanged.

Luna (OpenAI) and Gemini transports were removed in the Meta-only cutover.
These tests pin the new contract: anything that is not Muse Spark 1.3
Contributor fails fast without sending a request, while key storage,
redaction and connection-test invalidation keep working.
"""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import AIError, DEFAULT_MODEL, RoutedKey, post_ai, _object, STR, run_ai_review, redact_error
from pcb_checker.step_help import answer_step
from pcb_checker.design_review import local_plan
from test_design_review import project
from test_ai_review import RESULT
import test_credentials


def meta_payload(model):
    return {'model': model, 'store': False, 'input': 'Preserve the supplied board evidence.',
            'system_instruction': 'Explain the selected issue.',
            'generation_config': {'max_output_tokens': 1400, 'thinking_level': 'low'},
            'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': _object({'answer': STR})}}


class NonMetaRejectionTests(unittest.TestCase):
    def test_luna_payload_is_rejected_before_any_network_use(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            with self.assertRaisesRegex(AIError, 'exclusively'):
                post_ai(meta_payload('gpt-6-luna'), 'sk-test')
        opener.assert_not_called()

    def test_gemini_payload_is_rejected_before_any_network_use(self):
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            with self.assertRaisesRegex(AIError, 'exclusively'):
                post_ai(meta_payload('gemini-3.5-flash-lite'), 'sk-test')
        opener.assert_not_called()

    def test_backup_keys_are_never_consulted(self):
        key = RoutedKey('sk-test', backup='gemini-secret')
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            with self.assertRaisesRegex(AIError, 'exclusively'):
                post_ai(meta_payload('gpt-6-luna'), key, timeout=180)
        opener.assert_not_called()
        self.assertFalse(key.providers_used)

    def test_review_with_luna_model_fails_without_network(self):
        p = project()
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            result = run_ai_review(p, key='sk-test', model='gpt-6-luna', provider='openai')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('exclusively', result['message'])
        opener.assert_not_called()
        self.assertNotIn('sk-test', json.dumps(result))

    def test_step_chat_with_luna_model_fails_without_network(self):
        p = project()
        group = p.review['groups'][0]
        action = local_plan(p)['actions'][0]
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            result = answer_step(p, group, action, 0, 'How?', [], key='sk-test', model='gpt-6-luna')
        self.assertEqual(result['status'], 'failed')
        self.assertIn('exclusively', result['message'])
        opener.assert_not_called()

    def test_meta_payload_still_routes(self):
        from test_ai_review import meta_api_response
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            handle = opener.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = json.dumps(meta_api_response()).encode()
            result = post_ai(meta_payload(DEFAULT_MODEL), 'sk-test')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_both_provider_secrets_are_redacted(self):
        self.assertEqual(redact_error('sk-test gemini-secret', RoutedKey('sk-test', backup='gemini-secret')), '[redacted] [redacted]')
        self.assertEqual(redact_error('one two', ['one', 'two']), '[redacted] [redacted]')


class LunaCredentialTests(unittest.TestCase):
    setUp = test_credentials.CredentialTests.setUp

    def test_luna_save_preserves_main_gemini_and_restores_both_after_restart(self):
        from pcb_checker.webapp import AppState
        self.state.settings({'key': 'main-gemini', 'remember_key': True})
        old = dict(self.saved)
        config = self.state.settings({'model': 'gpt-6-luna', 'openai_key': 'sk-test-openai', 'remember_key': True})
        for account, value in old.items():
            self.assertEqual(self.saved[account], value)
        self.assertTrue(config['gemini_key_saved'])
        self.assertTrue(config['openai_key_saved'])
        self.assertNotIn('main-gemini', json.dumps(config))
        self.assertNotIn('sk-test-openai', json.dumps(config))
        second = AppState(self.folder, self.folder / 'reports')
        self.addCleanup(second.close)
        second.keychain_thread.join(2)
        self.assertEqual(second.model, 'gpt-6-luna')
        self.assertEqual(second.openai_key, 'sk-test-openai')
        # Cross-provider fallback is gone; the saved Gemini key list is returned as-is.
        self.assertEqual(second.keys_for_ai(), ['main-gemini'])
        second.settings({'model': 'gpt-6-luna', 'clear_key': True})
        self.assertFalse(self.saved)
        self.assertFalse(second.keys_for_ai())
        self.assertFalse(second.key)
        for path in (self.folder / 'reports').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'[REDACTED]', path.read_bytes())

    def test_connection_result_is_invalidated_when_credentials_change(self):
        self.state.settings({'model': 'gpt-6-luna', 'openai_key': 'sk-first', 'remember_key': False})
        result = {'status': 'completed', 'message': 'OpenAI answered.'}
        self.state.job = {"events": []}
        with patch('pcb_checker.webapp.check_ai_connection', return_value=result):
            self.state._work('connection', {}, None, None, RoutedKey('sk-first'), 'gpt-6-luna', True)
        self.assertEqual(self.state.config()['connection_test']['status'], 'completed')
        self.state.settings({'model': 'gpt-6-luna', 'openai_key': 'sk-new', 'remember_key': False})
        self.assertIsNone(self.state.config()['connection_test'])

    def test_selecting_luna_without_primary_keeps_gemini_key_visible(self):
        self.state.settings({'key': 'main-gemini', 'remember_key': True})
        config = self.state.settings({'model': 'gpt-6-luna', 'remember_key': True})
        self.assertTrue(config['gemini_key_configured'])
        self.assertFalse(config['openai_key_configured'])
        self.assertEqual(self.state.key, 'main-gemini')


if __name__ == '__main__':
    unittest.main()
