"""Cost controls use fake HTTP responses; these tests never call a provider."""
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import reviewer
from pcb_checker.ai.usage import request_budget, current_budget
from pcb_checker.models import Project
from pcb_checker.webapp import AppState, AppError


class UsageTests(unittest.TestCase):
    def setUp(self):
        reviewer._REVIEW_CACHE.clear()

    def send(self, provider='meta'):
        return reviewer._request('https://example.invalid', {}, {}, provider, max_attempts=3)

    def test_limit_counts_real_attempts_and_resets_between_actions(self):
        with patch.object(reviewer, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(b'{}')
            with request_budget() as budget:
                self.send()
                with request_budget('complete', 'repair'):
                    self.send()
                self.assertEqual(budget.requests, 2)
            self.assertIsNone(current_budget())
            # Legacy mode names map to the single complete tier.
            with request_budget('economy', 'repair'):
                for _ in range(20):
                    self.send()
                with self.assertRaisesRegex(reviewer.AIError, '20-request limit'):
                    self.send()
            # The refused 21st request sends nothing.
            self.assertEqual(opener.return_value.open.call_count, 22)

    def test_transient_retries_cannot_exceed_budget(self):
        with patch.object(reviewer, 'build_opener') as opener, patch.object(reviewer, '_pace_gemini_request'), patch.object(reviewer.time, 'sleep'):
            opener.return_value.open.side_effect = lambda *a, **k: (_ for _ in ()).throw(
                HTTPError('https://example.invalid', 503, 'busy', {}, io.BytesIO(b'{}')))
            with request_budget() as budget, self.assertRaisesRegex(reviewer.AIError, 'HTTP 503'):
                self.send('meta')
            self.assertEqual(budget.requests, 3)
        # Hitting the tier ceiling stops further requests before any HTTP attempt.
        with patch.object(reviewer, 'build_opener') as opener:
            opener.return_value.open.side_effect = lambda *a, **k: io.BytesIO(b'{}')
            with request_budget('complete', 'connection') as budget:
                self.send()
                self.send()
                with self.assertRaisesRegex(reviewer.AIError, '2-request limit'):
                    self.send()
            self.assertEqual(budget.requests, 2)
            self.assertEqual(opener.return_value.open.call_count, 2)
            self.assertEqual(opener.return_value.open.call_count, 2)

    def test_failed_request_consumes_one_budget_slot_without_hidden_retries(self):
        payload={'model': reviewer.DEFAULT_MODEL, 'input': 'evidence',
                 'response_format': {'schema': reviewer._object({'answer': reviewer.STR})}}
        with patch.object(reviewer, 'build_opener') as opener:
            opener.return_value.open.side_effect = HTTPError('https://example.invalid', 503, 'busy', {}, io.BytesIO(b'{}'))
            with request_budget('economy', 'connection') as budget, self.assertRaisesRegex(reviewer.AIError, 'HTTP 503'):
                reviewer.post_ai(payload, 'sk-fake', max_attempts=1, timeout=60)
            self.assertEqual(budget.requests, 1)
            self.assertEqual(opener.return_value.open.call_count, 1)
        with patch.object(reviewer, 'build_opener') as opener:
            with self.assertRaisesRegex(reviewer.AIError, 'exclusively'):
                reviewer.post_ai({'model': 'gpt-6-luna'}, reviewer.RoutedKey('sk-fake', backup='fake-backup'))
            opener.assert_not_called()

    def test_complete_budgets_apply_without_deep_fanout(self):
        result={'status':'completed', 'result':{}, 'usage':{}, 'elapsed_seconds':0}
        with patch.object(reviewer, '_run_issue_review', return_value=result) as review, patch('pcb_checker.ai.circuit_audit.run_circuit_audit') as audit:
            actual=reviewer.run_ai_review(Project(archive='test'), key='fake', design=True)
            self.assertTrue(review.call_args.kwargs['economy'])
            self.assertFalse(review.call_args.kwargs['functional_followup'])
            audit.assert_not_called()
            self.assertEqual(actual['request_usage']['request_limit'], 12)

    def test_only_identical_validated_reviews_are_reused(self):
        project=Project(archive='test')
        good={'status':'completed', 'result':{}, 'usage':{'input_tokens':100}, 'elapsed_seconds':1, 'rejected_items':[]}
        with patch.object(reviewer, '_run_ai_review', side_effect=lambda *a, **k:copy.deepcopy(good)) as review:
            first=reviewer.run_ai_review(project, key='fake', model='model', design=True)
            reused=reviewer.run_ai_review(project, key='fake', model='model', design=True)
            self.assertEqual(review.call_count, 1)
            self.assertTrue(reused['reused']); self.assertEqual(reused['usage'], {})
            self.assertEqual(first['usage']['input_tokens'], 100)
            project.provenance['source_hashes']={'board':'changed'}
            reviewer.run_ai_review(project, key='fake', model='model', design=True)
            reviewer.run_ai_review(project, key='new-key', model='model', design=True)
            reviewer.run_ai_review(project, key='new-key', model='model', design=True, question='New constraint')
            self.assertEqual(review.call_count, 4)

    def test_rejected_reviews_are_not_cached(self):
        project=Project(archive='test')
        partial={'status':'completed', 'result':{}, 'rejected_items':[{'group_id':'R001'}]}
        with patch.object(reviewer, '_run_ai_review', side_effect=lambda *a, **k:copy.deepcopy(partial)) as review:
            for _ in range(2):reviewer.run_ai_review(project, key='fake', design=True)
            self.assertEqual(review.call_count, 2)

    def test_usage_setting_defaults_to_complete_and_persists_without_network(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(reviewer, 'build_opener') as opener:
            state=AppState(folder, Path(folder)/'reports')
            try:
                self.assertEqual(state.config()['ai_usage_mode'], 'complete')
                self.assertEqual(state.config()['ai_request_limits']['review'], 12)
                self.assertEqual(state.config()['ai_request_limits']['repair'], 20)
                for invalid in ('unlimited', None, [], {}):
                    with self.assertRaises(AppError):state.settings({'ai_usage_mode':invalid})
                state.settings({'ai_usage_mode':'complete','remember_key':False})
                self.assertEqual(state.config()['ai_request_limits']['chat'], 4)
                second=AppState(folder, Path(folder)/'reports')
                try:self.assertEqual(second.config()['ai_usage_mode'], 'complete')
                finally:second.close()
                # Legacy stored modes migrate to the single tier.
                state.store.preference('ai_usage_mode', 'economy')
                self.assertEqual(state.config()['ai_usage_mode'], 'complete')
                self.assertEqual(state.settings({'ai_usage_mode': 'detailed'})['ai_usage_mode'], 'complete')
                opener.assert_not_called()
            finally:state.close()


if __name__ == '__main__':unittest.main()
