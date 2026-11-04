"""Coverage Proof and Investigation Replay: projections of saved records."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import investigation as workflow, ledger, replay, reviewer
from test_circuit_audit import fixture
from test_investigation import plan


def pipeline_for(claims=(), events=(), model=None, tasks=(), limitations=()):
    book = ledger.new_ledger({'board': 'x'})
    for cid, origin, statement, status, questions in claims:
        book['claims'].append({'id': cid, 'origin': origin, 'statement': statement,
                               'scope': {'components': [], 'nets': [], 'pins': []}, 'evidence': [],
                               'assumptions': [], 'agent_sureness': None, 'gauge_sureness': None,
                               'status': status, 'verification': 'deterministic',
                               'open_questions': list(questions)})
    book['events'] = list(events)
    ledger.finalize(book)
    return {'ledger': book, 'product_model': model, 'tasks': list(tasks), 'limitations': list(limitations)}


class ProofTests(unittest.TestCase):
    def test_counts_derive_from_recorded_claims(self):
        pipeline = pipeline_for([
            ('H001', 'scope', '[PWR-001] a', 'supported', []),
            ('H002', 'circuit', '[DSG-001] b', 'unresolved', ['Why?']),
            ('H003', 'vector_plan', 'free text', 'unresolved', [])])
        proof = replay.coverage_proof(pipeline)
        self.assertEqual(proof['claims_total'], 3)
        self.assertEqual(proof['by_status']['supported'], 1)
        self.assertEqual(proof['by_status']['unresolved'], 2)
        self.assertEqual(proof['by_origin'], {'scope': 1, 'circuit': 1, 'vector_plan': 1})
        self.assertEqual(proof['by_mode'], {'PWR-001': 1, 'DSG-001': 1, 'unclassified': 1})
        self.assertEqual(len(proof['unresolved']), 2)
        self.assertEqual(proof['unresolved'][0]['open_questions'], ['Why?'])

    def test_gaps_limitations_and_tasks_carried_through(self):
        event = {'seq': 1, 'kind': 'doubt_assessed', 'actor': 'doubt', 'detail': 'd', 'claim_id': None,
                 'data': {'gaps': {'unexamined_claimable': ['U9'], 'unexamined_other': [],
                                   'unexamined_nets': []}}}
        pipeline = pipeline_for([], [event],
                                model={'intent_questions': [{'question': 'What is it?'}]},
                                tasks=[{'id': 'I001', 'tool': 'trace_connections', 'references': ['U1'],
                                        'status': 'limited', 'limitations': ['gap']}],
                                limitations=['pipeline caveat'])
        pipeline['ledger']['limitations'].append('ledger caveat')
        proof = replay.coverage_proof(pipeline)
        self.assertEqual(proof['gaps']['unexamined_claimable'], ['U9'])
        self.assertEqual(proof['tool_runs'][0]['status'], 'limited')
        self.assertEqual(proof['intent_questions'], [{'question': 'What is it?'}])
        self.assertIn('ledger caveat', proof['limitations'])
        self.assertIn('pipeline caveat', proof['limitations'])

    def test_missing_ledger_returns_none(self):
        self.assertIsNone(replay.coverage_proof({}))
        self.assertIsNone(replay.coverage_proof(None))
        self.assertEqual(replay.replay_timeline({}), [])
        self.assertEqual(replay.replay_timeline(None), [])


class TimelineTests(unittest.TestCase):
    def test_events_project_in_order_with_titles_and_statements(self):
        events = [
            {'seq': 2, 'kind': 'status', 'actor': 'verdict', 'detail': 'h -> s: ok', 'claim_id': 'H001',
             'data': {}},
            {'seq': 1, 'kind': 'hypothesized', 'actor': 'scope', 'detail': 's', 'claim_id': 'H001',
             'data': {}},
            {'seq': 3, 'kind': 'future_kind', 'actor': '?', 'detail': 'd', 'claim_id': None, 'data': {}}]
        pipeline = pipeline_for([('H001', 'scope', '[PWR-001] ' + 's' * 300, 'supported', [])], events)
        timeline = replay.replay_timeline(pipeline)
        self.assertEqual([e['seq'] for e in timeline], [1, 2, 3])
        self.assertEqual(timeline[0]['title'], 'Hypothesis recorded')
        self.assertEqual(timeline[1]['title'], 'Status change')
        self.assertEqual(timeline[2]['title'], 'future_kind')
        self.assertEqual(len(timeline[0]['statement']), 200)

    def test_intent_questions_appended_after_events(self):
        pipeline = pipeline_for([], [], model={'intent_questions': [
            {'question': 'Q?', 'context': 'ctx'}]})
        timeline = replay.replay_timeline(pipeline)
        self.assertEqual(len(timeline), 1)
        self.assertEqual(timeline[0]['kind'], 'intent_question')
        self.assertEqual(timeline[0]['seq'], 1)
        self.assertEqual(timeline[0]['context'], 'ctx')


class WiringTests(unittest.TestCase):
    def test_finish_attaches_proof_and_replay(self):
        project = fixture()
        packet = reviewer.evidence_packet(project)
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})):
            _, trace, _ = workflow.investigate(project, packet, key='secret-key', model='t', provider='g')
        result = workflow.finish(trace, {'status': 'completed',
                                         'coverage': {'validated_actions': 0, 'requested_actions': 0}})
        proof, replayed = result['pipeline']['coverage_proof'], result['pipeline']['replay']
        self.assertEqual(proof['claims_total'], len(trace['ledger']['claims']))
        self.assertEqual(proof['by_status']['supported'], 5)
        self.assertEqual(proof['by_status']['unresolved'], 3)
        self.assertEqual(len(replayed), len(trace['ledger']['events']) + 1)
        self.assertEqual(replayed[-1]['kind'], 'intent_question')
        self.assertNotIn('secret-key', json.dumps(proof))
        self.assertNotIn('secret-key', json.dumps(replayed))

    def test_skipped_run_reports_honest_zeros(self):
        from pcb_checker.ai.usage import request_budget
        project = fixture()
        # A fresh complete-tier budget is not scarce; spend it down to the
        # <3-request reserve so the skip path is genuinely exercised.
        with request_budget() as budget:
            while budget.limit - budget.requests >= 3:
                budget.consume()
            _, trace, _ = workflow.investigate(project, reviewer.evidence_packet(project),
                                               key='k', model='t', provider='g')
        result = workflow.finish(trace, {'status': 'completed', 'coverage': {}})
        proof = result['pipeline']['coverage_proof']
        self.assertEqual(proof['claims_total'], 0)
        self.assertTrue(proof['limitations'])


if __name__ == '__main__':
    unittest.main()
