"""Contributor usage recording, pricing math, budgets and tracker state. All Meta API calls mocked."""
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.meta import (MODEL_ID, PRICING_USD_PER_M, estimate_cost_usd, meta_action,
                                 set_usage_recorder)
from pcb_checker.ai.reviewer import AIError, post_ai, _object, STR
from pcb_checker.storage import WorkspaceStore
from pcb_checker.webapp import AppState, AppError
def payload():
    return {'model': 'meta/placeholder', 'store': False, 'input': 'Preserve the supplied board evidence.',
            'system_instruction': 'Explain the selected issue.',
            'generation_config': {'max_output_tokens': 1400, 'thinking_level': 'low'},
            'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': _object({'answer': STR})}}

MODEL = 'meta/' + MODEL_ID


def reply(text, usage=None):
    response = {'choices': [{'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}]}
    if usage is not None:
        response['usage'] = usage
    return response


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.records = []
        set_usage_recorder(self.records.append)

    def tearDown(self):
        set_usage_recorder(None)

    def packet(self):
        packet = payload()
        packet['model'] = MODEL
        return packet

    def test_completed_request_records_tokens_and_price_snapshot(self):
        usage = {'prompt_tokens': 1000, 'completion_tokens': 500, 'total_tokens': 1500,
                 'prompt_tokens_details': {'cached_tokens': 200}}
        with patch('pcb_checker.ai.reviewer._request', return_value=reply('{"answer":"ok"}', usage)):
            with meta_action('review'):
                result = post_ai(self.packet(), 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(self.records), 1)
        record = self.records[0]
        self.assertEqual(len(record['request_id']), 32)
        self.assertEqual(record['model'], MODEL)
        self.assertEqual(record['action'], 'review')
        self.assertEqual(record['status'], 'completed')
        self.assertEqual((record['input_tokens'], record['output_tokens'], record['cached_input_tokens']),
                         (1000, 500, 200))
        self.assertEqual({k: record[k] for k in ('input_usd_per_m', 'output_usd_per_m', 'cached_usd_per_m')},
                         {'input_usd_per_m': 0.10, 'output_usd_per_m': 0.20, 'cached_usd_per_m': 0.002})
        self.assertNotIn('meta-test-secret', json.dumps(record))

    def test_incomplete_response_bills_reported_tokens(self):
        response = reply('{"partial":true}',
                         {'prompt_tokens': 100, 'completion_tokens': 900, 'total_tokens': 1000})
        response['choices'][0]['finish_reason'] = 'length'
        with patch('pcb_checker.ai.reviewer._request', return_value=response):
            with self.assertRaises(AIError):
                post_ai(self.packet(), 'meta-test-secret')
        self.assertEqual([(r['status'], r['input_tokens'], r['output_tokens']) for r in self.records],
                         [('incomplete', 100, 900)])

    def test_failed_attempt_records_zeros_and_missing_usage_completes(self):
        with patch('pcb_checker.ai.reviewer._request', side_effect=AIError('Unavailable')):
            with self.assertRaises(AIError):
                post_ai(self.packet(), 'meta-test-secret')
        with patch('pcb_checker.ai.reviewer._request', return_value=reply('{"answer":"ok"}')):
            result = post_ai(self.packet(), 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual([(r['status'], r['input_tokens'], r['output_tokens']) for r in self.records],
                         [('failed', 0, 0), ('completed', 0, 0)])

    def test_recorder_failure_never_breaks_the_call(self):
        set_usage_recorder(lambda record: (_ for _ in ()).throw(OSError('disk gone')))
        with patch('pcb_checker.ai.reviewer._request', return_value=reply('{"answer":"ok"}')):
            result = post_ai(self.packet(), 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')

    def test_contributor_pricing_and_cost_math(self):
        self.assertEqual(PRICING_USD_PER_M, {'input': 0.10, 'cached_input': 0.002, 'output': 0.20})
        self.assertAlmostEqual(estimate_cost_usd(1000000, 1000000, 500000), 0.251)
        self.assertAlmostEqual(estimate_cost_usd(1000, 500, 200), 0.0001804)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = WorkspaceStore(Path(self.temp.name))

    def record(self, **overrides):
        base = {'request_id': 'r1', 'at': '2026-10-27T10:00:00+00:00', 'model': MODEL,
                'action': 'review', 'status': 'completed', 'input_tokens': 1000000,
                'output_tokens': 1000000, 'cached_input_tokens': 0,
                'input_usd_per_m': 0.10, 'output_usd_per_m': 0.20, 'cached_usd_per_m': 0.002}
        return base | overrides

    def test_duplicate_request_id_is_stored_once(self):
        self.store.record_meta_usage(self.record())
        self.store.record_meta_usage(self.record())
        summary = self.store.meta_usage_summary(budget_usd=75, warn_usd=60, warn_high_usd=70,
                                                model=MODEL, pricing=PRICING_USD_PER_M)
        self.assertEqual(summary['requests'], 1)

    def test_summary_math_levels_and_failed_exclusion(self):
        self.store.record_meta_usage(self.record(request_id='r1'))
        self.store.record_meta_usage(self.record(request_id='r2', status='failed'))
        self.store.record_meta_usage(self.record(request_id='r3', input_tokens=1000, output_tokens=0,
                                                 cached_input_tokens=1000))
        summary = self.store.meta_usage_summary(budget_usd=75, warn_usd=60, warn_high_usd=70,
                                                model=MODEL, pricing=PRICING_USD_PER_M)
        self.assertEqual(summary['requests'], 2)
        self.assertEqual(summary['input_tokens'], 1001000)
        self.assertEqual(summary['cached_input_tokens'], 1000)
        self.assertAlmostEqual(summary['estimated_cost_usd'], 0.300002)
        self.assertEqual(summary['level'], 'ok')
        low = self.store.meta_usage_summary(budget_usd=0.30, warn_usd=0.20, warn_high_usd=0.25,
                                            model=MODEL, pricing=PRICING_USD_PER_M)
        self.assertEqual(low['level'], 'over')
        self.assertAlmostEqual(low['remaining_usd'], -0.000002)

    def test_invalid_records_rejected(self):
        for bad in ({}, {'request_id': ''}, self.record(status='billed'),
                    self.record(input_tokens=-1), self.record(input_tokens=1.5),
                    self.record(input_usd_per_m=-0.1), self.record(input_usd_per_m=True)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.record_meta_usage(bad)

    def test_no_secrets_in_usage_table(self):
        self.store.record_meta_usage(self.record())
        with sqlite3.connect(self.store.path) as db:
            dump = json.dumps([list(r) for r in db.execute('SELECT * FROM meta_usage')])
        self.assertNotIn('meta-test-secret', dump)


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.state = AppState(self.folder, self.folder / 'reports')
        self.addCleanup(self.state.close)

    def test_defaults_and_config_shape(self):
        summary = self.state.usage_summary()
        self.assertEqual((summary['requests'], summary['estimated_cost_usd'], summary['level']),
                         (0, 0.0, 'ok'))
        self.assertEqual((summary['budget_usd'], summary['warn_usd'], summary['warn_high_usd']),
                         (75.0, 60.0, 70.0))
        self.assertEqual(summary['model'], MODEL)
        self.assertEqual(self.state.config()['usage']['budget_usd'], 75.0)

    def test_save_valid_budget(self):
        summary = self.state.save_usage_budget({'budget_usd': 50, 'warn_usd': 40, 'warn_high_usd': 45})
        self.assertEqual((summary['budget_usd'], summary['warn_usd'], summary['warn_high_usd']),
                         (50.0, 40.0, 45.0))

    def test_save_invalid_budget_rejected(self):
        for data in ({'budget_usd': -1, 'warn_usd': 0, 'warn_high_usd': 0},
                     {'budget_usd': '50', 'warn_usd': 40, 'warn_high_usd': 45},
                     {'budget_usd': True, 'warn_usd': 40, 'warn_high_usd': 45},
                     {'budget_usd': 50, 'warn_usd': 46, 'warn_high_usd': 45},
                     {'budget_usd': 50, 'warn_usd': 40}):
            with self.subTest(data=data), self.assertRaises(AppError):
                self.state.save_usage_budget(data)
        self.assertEqual(self.state.usage_summary()['budget_usd'], 75.0)

    def test_summary_serves_last_good_state_during_outage(self):
        first = self.state.usage_summary()
        self.assertIsNotNone(first)
        self.state.store.path.write_bytes(b'not a database')
        self.assertEqual(self.state.usage_summary(), first)


if __name__ == '__main__':
    unittest.main()
