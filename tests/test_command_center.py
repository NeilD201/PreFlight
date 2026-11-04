"""Command Center backend: /api/investigation payload shape, no credentials."""
import json
import re
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppState

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = 'unit-test-command-key-sentinel-002'


def bare_state(project=None):
    state = AppState.__new__(AppState)
    state.lock = threading.Lock()
    state.project = project
    return state


class InvestigationDataTests(unittest.TestCase):
    def test_empty_without_project_or_pipeline(self):
        self.assertEqual(bare_state(None).investigation_data(), {'status': 'empty'})
        project = SimpleNamespace(ai_review={'status': 'completed'})
        self.assertEqual(bare_state(project).investigation_data(), {'status': 'empty'})

    def test_live_payload_shape_and_no_key_material(self):
        pipeline = {'status': 'limited', 'ledger': {'claims': [], 'events': []},
                    'replay': [], 'tasks': [], 'coverage_proof': {}}
        review = {'status': 'completed', 'model': 'meta/muse-spark-1.3-contributor',
                  'pipeline': pipeline, 'api_key': SENTINEL,
                  'result': {'actions': [], 'questions': [], 'summary': '', 'limitations': []},
                  'usage': {}, 'request_usage': {}}
        data = bare_state(SimpleNamespace(ai_review=review)).investigation_data()
        self.assertEqual(data['pipeline'], pipeline)
        self.assertEqual(data['model'], 'meta/muse-spark-1.3-contributor')
        self.assertEqual(set(data['result']), {'actions', 'questions', 'summary', 'limitations'})
        self.assertNotIn(SENTINEL, json.dumps(data))


    def test_running_job_serves_live_snapshot_then_empty(self):
        live = {'status': 'running', 'steps': [], 'tasks': [],
                'ledger': {'claims': [], 'events': [{'seq': 1, 'kind': 'hypothesized'}]},
                'replay': [{'seq': 1, 'kind': 'hypothesized', 'actor': 'scope'}],
                'coverage_proof': {}, 'elapsed_seconds': 1.5}
        state = bare_state(SimpleNamespace(ai_review={'status': 'running'}))
        state.live_pipeline = live
        state.job = {'status': 'running'}
        data = state.investigation_data()
        self.assertEqual(data['status'], 'running')
        self.assertEqual(data['pipeline'], live)
        self.assertEqual(data['result'], {'actions': [], 'questions': [], 'summary': '', 'limitations': []})
        self.assertNotIn(SENTINEL, json.dumps(data))
        state.job = {'status': 'completed'}
        self.assertEqual(state.investigation_data(), {'status': 'empty'})
        state.live_pipeline = {'status': 'running', 'api_key': SENTINEL}
        state.job = {'status': 'completed'}
        self.assertNotIn(SENTINEL, json.dumps(state.investigation_data()))


class ReplayFixtureTests(unittest.TestCase):
    def test_fixture_matches_live_shape_without_secrets(self):
        fix = json.loads((ROOT / 'web' / 'command-replay.json').read_text())
        for key in ('pipeline', 'result', 'usage', 'request_usage', 'model'):
            self.assertIn(key, fix)
        for key in ('ledger', 'replay', 'tasks', 'coverage_proof', 'status'):
            self.assertIn(key, fix['pipeline'])
        self.assertGreater(len(fix['pipeline']['ledger']['claims']), 0)
        self.assertGreater(len(fix['pipeline']['replay']), 0)
        strings = []

        def walk(o):
            if isinstance(o, str):
                strings.append(o)
            elif isinstance(o, dict):
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(fix)
        suspicious = [s for s in set(strings)
                      if re.fullmatch(r'[A-Za-z0-9_\-]{25,}', s)
                      and not re.fullmatch(r'[0-9a-f]{20}([0-9a-f]{44})?', s)]
        self.assertEqual(suspicious, [])


if __name__ == '__main__':
    unittest.main()
