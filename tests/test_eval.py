"""Evaluation benchmark: labeled contracts, determinism, no-provider proof."""
import io
import json
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
import eval_benchmark
import eval_cases


def snapshot(book):
    return {c['id']: (c['statement'], c['status'], c.get('fix_gate', {}).get('status'))
            for c in book['claims']}


class EvalTests(unittest.TestCase):
    def test_every_case_meets_its_contract(self):
        for case in eval_cases.CASES:
            with self.subTest(case=case['id']):
                record = eval_benchmark.run_case(case)
                self.assertTrue(record['passed'], record['detail'])

    def test_pipeline_is_deterministic(self):
        for case in eval_cases.CASES:
            if 'fix_expected' in case:
                continue
            with self.subTest(case=case['id']):
                first = snapshot(eval_benchmark.run_pipeline(case['packet'], case.get('setup')))
                second = snapshot(eval_benchmark.run_pipeline(case['packet'], case.get('setup')))
                self.assertEqual(first, second)
                self.assertGreaterEqual(len(first), len(case.get('present', ())))

    def test_benchmark_needs_no_provider(self):
        def nope(*args, **kwargs):
            raise AssertionError('provider must not be called')
        with patch('pcb_checker.ai.reviewer._post', side_effect=nope), \
                patch('pcb_checker.ai.reviewer.post_ai', side_effect=nope):
            report = eval_benchmark.summarize([eval_benchmark.run_case(c) for c in eval_cases.CASES])
        self.assertEqual(report['passed'], report['cases'])
        self.assertEqual(report['unsupported_supported'], 0)
        self.assertEqual(report['unresolved_links'], 0)

    def test_report_is_versioned_and_structured(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            status = eval_benchmark.main(['eval_benchmark.py', '--json'])
        self.assertEqual(status, 0)
        report = json.loads(buffer.getvalue())
        self.assertEqual(report['eval_version'], eval_cases.EVAL_VERSION)
        for key in ('cases', 'passed', 'recall', 'forbidden_hits', 'unsupported_supported',
                    'unresolved_links', 'fix_validity', 'records'):
            self.assertIn(key, report)
        self.assertEqual(report['recall'], [13, 13])
        self.assertEqual(report['fix_validity'], [2, 2])


if __name__ == '__main__':
    unittest.main()
