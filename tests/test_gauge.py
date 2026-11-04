"""GAUGE audit: validation, budget gates, scoring and escalation."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import gauge, ledger, reviewer
from pcb_checker.ai.usage import request_budget
from test_circuit_audit import fixture


def entry(cid, score, reasoning='Solid.', cited=(), disagreements=()):
    return {'claim_id': cid, 'sureness': score, 'reasoning': reasoning,
            'disagreements': list(disagreements), 'cited_ids': list(cited)}


def material_ledger():
    book = ledger.new_ledger({})
    for cid, statement in (('H001', '[PWR-001] a.'), ('H002', '[IF-002] b.')):
        ledger.add_claim(book, origin='scope', statement=statement,
                         scope={'components': [], 'nets': [], 'pins': []})
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, 'supported' if cid == 'H001' else 'unresolved',
                          reason='r', actor='t')
    return book


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def test_wrong_claim_set_rejected(self):
        with self.assertRaises(reviewer.AIError):
            gauge.validate_audits({'audits': [entry('H999', 50)]}, ['H001'], self.packet)

    def test_sureness_must_be_integer_in_range(self):
        for bad in ('high', '101', '-1', '', '49.5', None, 49, True):
            with self.subTest(bad=bad), self.assertRaises(reviewer.AIError):
                gauge.validate_audits({'audits': [entry('H001', bad)]}, ['H001'], self.packet)
        gauge.validate_audits({'audits': [entry('H001', '0'), entry('H002', '100')]},
                              ['H001', 'H002'], self.packet)

    def test_reasoning_required_and_citations_resolve(self):
        with self.assertRaises(reviewer.AIError):
            gauge.validate_audits({'audits': [entry('H001', '50', '  ')]}, ['H001'], self.packet)
        with self.assertRaises(reviewer.AIError):
            gauge.validate_audits({'audits': [entry('H001', '50', 'ok', ['N:NOPE'])]},
                                  ['H001'], self.packet)
        gauge.validate_audits({'audits': [entry('H001', '50', 'ok', ['C:U1'])]}, ['H001'], self.packet)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def test_success_scores_and_escalates_weak_only(self):
        book = material_ledger()
        next(c for c in book['claims'] if c['id'] == 'H001')['agent_sureness'] = 99
        answer = {'audits': [entry('H001', '99', 'Strong.', disagreements=[]),
                             entry('H002', '60', 'Thin evidence.', disagreements=['Basis is thin.'])]}
        with request_budget('detailed'), \
                patch.object(gauge, '_ask', return_value=(answer, {'input_tokens': 7})) as ask:
            usage = gauge.audit(self.packet, book, key='k', model='m', provider='m')
        ask.assert_called_once()
        self.assertEqual(usage, {'input_tokens': 7})
        by_id = {c['id']: c for c in book['claims']}
        self.assertEqual(by_id['H001']['gauge_sureness'], 99)
        self.assertEqual(by_id['H002']['gauge_sureness'], 60)
        self.assertFalse(any('GAUGE scores' in q for q in by_id['H001']['open_questions']))
        self.assertTrue(any('GAUGE scores this 60/100' in q for q in by_id['H002']['open_questions']))
        self.assertIn('audit_completed', [e['kind'] for e in book['events']])
        self.assertNotIn('secret', json.dumps(book))

    def test_sharp_disagreement_escalates_despite_high_score(self):
        book = material_ledger()
        next(c for c in book['claims'] if c['id'] == 'H001')['agent_sureness'] = 99
        answer = {'audits': [entry('H001', '99', 'ok.'), entry('H002', '99', 'ok.')]}
        next(c for c in book['claims'] if c['id'] == 'H002')['agent_sureness'] = 40
        with request_budget('detailed'), patch.object(gauge, '_ask', return_value=(answer, {})):
            gauge.audit(self.packet, book, key='k', model='m', provider='m')
        by_id = {c['id']: c for c in book['claims']}
        self.assertFalse(any('GAUGE scores' in q for q in by_id['H001']['open_questions']))
        self.assertTrue(any('GAUGE scores' in q for q in by_id['H002']['open_questions']))

    def test_no_budget_and_low_budget_skip(self):
        book = material_ledger()
        with patch.object(gauge, '_ask') as ask:
            gauge.audit(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        with request_budget('detailed') as budget, patch.object(gauge, '_ask') as ask:
            budget.requests = 9
            gauge.audit(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        self.assertTrue(any(e['kind'] == 'gauge_skipped' for e in book['events']))

    def test_failure_records_redacted_unavailable(self):
        book = material_ledger()
        with request_budget('detailed'), \
                patch.object(gauge, '_ask', side_effect=reviewer.AIError('bad secret-key')):
            gauge.audit(self.packet, book, key='secret-key', model='m', provider='m')
        event = next(e for e in book['events'] if e['kind'] == 'gauge_unavailable')
        self.assertNotIn('secret-key', event['detail'])

    def test_single_timeout_retries_once(self):
        native = {'choices': [{'message': {'content': '{"audits":[]}'}, 'finish_reason': 'stop'}],
                  'usage': {'prompt_tokens': 5, 'completion_tokens': 5, 'total_tokens': 10}}
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(native).encode(),
        })()
        with request_budget('detailed'), \
             patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [TimeoutError('slow'), response]
            value, _ = gauge._ask(self.packet, [], key='k', model='meta/muse-spark-1.3-contributor',
                                  provider='meta', progress=None)
        self.assertEqual(value, {'audits': []})
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_transports_parse(self):
        native = {'status': 'completed', 'usage': {},
                  'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': '{"audits":[]}'}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=native):
            value, _ = gauge._ask(self.packet, [], key='k', model='m', provider='gemini', progress=None)
        self.assertEqual(value, {'audits': []})
        native = {'status': 'incomplete', 'usage': {}}
        with patch('pcb_checker.ai.reviewer._post', return_value=native), \
                self.assertRaises(reviewer.AIError):
            gauge._ask(self.packet, [], key='k', model='m', provider='meta', progress=None)

    def test_context_lists_resolvable_evidence_ids(self):
        claim = {'id': 'H001', 'statement': 's', 'status': 'supported', 'agent_sureness': None,
                 'scope': {'components': ['U1'], 'nets': ['VCC'], 'pins': []}, 'open_questions': [],
                 'consultations': []}
        sent = gauge._context(self.packet, [claim])[0]
        self.assertEqual(sent['evidence_ids'], ['C:U1', 'N:VCC'])
        self.assertIn('C:U1', gauge.INSTRUCTIONS)
        self.assertIn('N:SDA', gauge.INSTRUCTIONS)

    def test_unavailable_event_carries_failure_diagnostics(self):
        book = material_ledger()
        boom = reviewer.AIError('Meta did not finish.', diagnostics={'finish_reason': 'length'})
        with request_budget('detailed'), patch.object(gauge, '_ask', side_effect=boom):
            gauge.audit(self.packet, book, key='k', model='m', provider='meta')
        event = next(e for e in book['events'] if e['kind'] == 'gauge_unavailable')
        self.assertEqual(event['data'], {'diagnostics': {'finish_reason': 'length'}})

    def test_audit_event_preserves_full_entry(self):
        book = material_ledger()
        answer = {'audits': [entry('H001', '90', 'Solid reasoning.', cited=['C:U1']),
                                entry('H002', '40', 'Thin.', disagreements=['Basis is thin.'])]}
        with request_budget('detailed'), \
                patch.object(gauge, '_ask', return_value=(answer, {})):
            gauge.audit(self.packet, book, key='k', model='m', provider='meta')
        first = next(e for e in book['events'] if e['kind'] == 'audit')
        self.assertEqual(first['data']['cited_ids'], ['C:U1'])
        self.assertEqual(first['data']['reasoning'], 'Solid reasoning.')
        self.assertEqual(book['claims'][0]['gauge_sureness'], 90)

    def test_completed_event_reports_audited_of_eligible(self):
        book = material_ledger()
        answer = {'audits': [entry('H001', '90'), entry('H002', '40')]}
        with request_budget('detailed'), \
                patch.object(gauge, '_ask', return_value=(answer, {})):
            gauge.audit(self.packet, book, key='k', model='m', provider='meta')
        done = next(e for e in book['events'] if e['kind'] == 'audit_completed')
        self.assertEqual(done['data']['eligible'], 2)
        self.assertIn('of 2 material', done['detail'])


if __name__ == '__main__':
    unittest.main()
