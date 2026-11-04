"""FAULT challenge: validation, budget gates, recording and fix proposals."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import fault, ledger, reviewer
from pcb_checker.ai.usage import request_budget
from test_circuit_audit import fixture


def critique(cid, verdict='stands', counter='', cited=(), missing='Check X.', proposed='no', **fix):
    data = {'claim_id': cid, 'verdict': verdict, 'counterevidence': counter,
            'cited_ids': list(cited), 'missing_check': missing,
            'proposed_fix': {'proposed': proposed, 'action': '', 'reference': '', 'pin': '',
                             'net': '', 'value': '', 'connection': '', 'verify_steps': []}}
    data['proposed_fix'].update(fix)
    return data


def material_ledger(packet):
    book = ledger.new_ledger({})
    ledger.add_claim(book, origin='scope', statement='[PWR-001] U1 decoupling.',
                     scope={'components': ['U1'], 'nets': ['VCC'], 'pins': ['U1.1']},
                     evidence_ids=['C:U1', 'N:VCC'])
    for cid in ('H001',):
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, 'supported', reason='r', actor='t')
    return book


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def test_wrong_claim_set_rejected(self):
        with self.assertRaises(reviewer.AIError):
            fault.validate_critiques({'critiques': [critique('H999')]}, ['H001'], self.packet)

    def test_non_standing_needs_counterevidence_and_missing_check(self):
        with self.assertRaises(reviewer.AIError):
            fault.validate_critiques({'critiques': [critique('H001', 'weakened')]}, ['H001'], self.packet)
        bad = critique('H001', 'stands', missing='  ')
        with self.assertRaises(reviewer.AIError):
            fault.validate_critiques({'critiques': [bad]}, ['H001'], self.packet)

    def test_unresolvable_citations_rejected(self):
        bad = critique('H001', 'stands', cited=['N:NOPE'])
        with self.assertRaises(reviewer.AIError):
            fault.validate_critiques({'critiques': [bad]}, ['H001'], self.packet)
        good = critique('H001', 'stands', cited=['C:U1', 'N:VCC'])
        self.assertEqual(fault.validate_critiques({'critiques': [good]}, ['H001'], self.packet)[0]['claim_id'], 'H001')

    def test_fix_proposal_needs_action_and_reference(self):
        bad = critique('H001', 'weakened', 'counter', proposed='yes', action='', reference='U1')
        with self.assertRaises(reviewer.AIError):
            fault.validate_critiques({'critiques': [bad]}, ['H001'], self.packet)
        good = critique('H001', 'weakened', 'counter', proposed='yes', action='Add cap', reference='U1')
        fault.validate_critiques({'critiques': [good]}, ['H001'], self.packet)


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def test_success_records_critique_fix_and_gate_b(self):
        book = material_ledger(self.packet)
        answer = {'critiques': [critique('H001', 'weakened', 'Cap value unproven.', ['C:U1'],
                                         'Confirm capacitance.', 'yes', action='Fit 100nF cap',
                                         reference='U1', pin='1', net='VCC', value='100nF',
                                         connection='Across U1 pin 1 to ground.',
                                         verify_steps=['Measure ripple.'])]}
        with request_budget('detailed'), \
                patch.object(fault, '_ask', return_value=(answer, {'input_tokens': 5})) as ask:
            usage = fault.challenge(self.packet, book, key='k', model='m', provider='meta')
        ask.assert_called_once()
        self.assertEqual(usage, {'input_tokens': 5})
        claim = next(c for c in book['claims'] if c['id'] == 'H001')
        self.assertEqual(claim['status'], 'supported')
        self.assertIn('Confirm capacitance.', claim['open_questions'])
        self.assertEqual(claim['proposed_fix']['reference'], 'U1')
        self.assertEqual(claim['fix_gate']['status'], 'structure_ok')
        self.assertTrue(any('not approved' in q for q in claim['fix_gate']['retest']))
        kinds = [e['kind'] for e in book['events']]
        for kind in ('challenge', 'fix_proposed', 'fix_verified', 'challenge_completed'):
            self.assertIn(kind, kinds)
        self.assertNotIn('secret', json.dumps(book))

    def test_substantiated_refutation_closes_unresolved_claim(self):
        book = material_ledger(self.packet)
        cid = ledger.add_claim(book, origin='scope', statement='[IF-002] timing.',
                               scope={'components': ['U1'], 'nets': ['BUS'], 'pins': []},
                               evidence_ids=['C:U1', 'N:BUS'])
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, 'unresolved', reason='r', actor='t')
        answer = {'critiques': [critique('H001', 'stands'),
                                critique(cid, 'refuted', 'Measured rise time passes.', ['C:U1'],
                                         'Recheck on hardware.')]}
        with request_budget('detailed'), patch.object(fault, '_ask', return_value=(answer, {})):
            fault.challenge(self.packet, book, key='k', model='m', provider='m')
        by_id = {c['id']: c for c in book['claims']}
        self.assertEqual(by_id['H001']['status'], 'supported')
        self.assertEqual(by_id[cid]['status'], 'contradicted')
        event = next(e for e in book['events'] if e['kind'] == 'challenge' and e['claim_id'] == cid)
        self.assertIn('refuted', event['detail'])

    def test_no_budget_skips_without_request(self):
        book = material_ledger(self.packet)
        with patch.object(fault, '_ask') as ask:
            self.assertEqual(fault.challenge(self.packet, book, key='k', model='m', provider='m'), {})
        ask.assert_not_called()
        self.assertTrue(any(e['kind'] == 'fault_skipped' for e in book['events']))

    def test_low_budget_skips_without_request(self):
        book = material_ledger(self.packet)
        with request_budget('detailed') as budget, patch.object(fault, '_ask') as ask:
            budget.requests = 8
            fault.challenge(self.packet, book, key='k', model='m', provider='m')
        ask.assert_not_called()
        self.assertTrue(any(e['kind'] == 'fault_skipped' for e in book['events']))

    def test_failure_records_redacted_unavailable(self):
        book = material_ledger(self.packet)
        with request_budget('detailed'), \
                patch.object(fault, '_ask', side_effect=reviewer.AIError('bad secret-key')):
            fault.challenge(self.packet, book, key='secret-key', model='m', provider='m')
        event = next(e for e in book['events'] if e['kind'] == 'fault_unavailable')
        self.assertNotIn('secret-key', event['detail'])

    def test_challenge_and_audit_run_inside_investigation_with_budget(self):
        from pcb_checker.ai import consult as consult_mod
        from pcb_checker.ai import gauge as gauge_mod
        from pcb_checker.ai import investigation as workflow
        from test_investigation import plan
        project = fixture()
        packet = reviewer.evidence_packet(project)
        ids = ['H001', 'H002', 'H003', 'H004']
        answers = {'critiques': [critique(cid, 'stands') for cid in ids]}
        replies = {'consultations': [{'claim_id': cid, 'question': 'Check X.', 'finding': 'answered',
                                      'answer': 'Confirmed.', 'cited_ids': []} for cid in ids]}
        audits = {'audits': [{'claim_id': cid, 'sureness': '90', 'reasoning': 'ok.',
                              'disagreements': [], 'cited_ids': []} for cid in ids]}
        with request_budget('detailed'), \
                patch.object(workflow, 'ask_plan', return_value=(plan(), {})), \
                patch.object(fault, '_ask', return_value=(answers, {'input_tokens': 5})), \
                patch.object(consult_mod, '_ask', return_value=(replies, {'input_tokens': 3})), \
                patch.object(gauge_mod, '_ask', return_value=(audits, {'input_tokens': 7})):
            _, trace, usage = workflow.investigate(project, packet, key='k', model='m', provider='m')
        kinds = [e['kind'] for e in trace['ledger']['events']]
        self.assertIn('challenge_completed', kinds)
        self.assertIn('consultation_completed', kinds)
        self.assertIn('audit_completed', kinds)
        self.assertEqual(usage, {'input_tokens': 15})
        by_id = {c['id']: c for c in trace['ledger']['claims']}
        self.assertEqual(by_id['H001']['gauge_sureness'], 90)
        self.assertTrue(any('GAUGE scores' in q for q in by_id['H001']['open_questions']))

    def test_single_timeout_retries_once(self):
        native = {'choices': [{'message': {'content': '{"critiques":[]}'}, 'finish_reason': 'stop'}],
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
            value, _ = fault._ask(self.packet, [], key='k', model='meta/muse-spark-1.3-contributor', provider='meta', progress=None)
        self.assertEqual(value, {'critiques': []})
        self.assertEqual(opener.return_value.open.call_count, 2)

    def test_meta_transport_parses(self):
        native = {'status': 'completed', 'usage': {'total_input_tokens': 3},
                  'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': '{"critiques":[]}'}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai', return_value=native) as send:
            value, usage = fault._ask(self.packet, [], key='k', model='m', provider='meta', progress=None)
        self.assertEqual(value, {'critiques': []})
        self.assertEqual(usage['total_input_tokens'], 3)
        self.assertEqual(send.call_args.args[0]['response_format']['mime_type'], 'application/json')

    def test_context_lists_resolvable_evidence_ids(self):
        claim = {'id': 'H001', 'statement': 's', 'status': 'supported',
                 'scope': {'components': ['U1'], 'nets': ['VCC'], 'pins': []}, 'open_questions': []}
        sent = fault._context(self.packet, [claim])[0]
        self.assertEqual(sent['evidence_ids'], ['C:U1', 'N:VCC'])
        self.assertIn('C:U1', fault.INSTRUCTIONS)
        self.assertIn('N:SDA', fault.INSTRUCTIONS)

    def test_unavailable_event_carries_failure_diagnostics(self):
        book = material_ledger(self.packet)
        boom = reviewer.AIError('Meta did not finish.', diagnostics={'finish_reason': 'length'})
        with request_budget('detailed'), patch.object(fault, '_ask', side_effect=boom):
            fault.challenge(self.packet, book, key='k', model='m', provider='meta')
        event = next(e for e in book['events'] if e['kind'] == 'fault_unavailable')
        self.assertEqual(event['data'], {'diagnostics': {'finish_reason': 'length'}})

    def test_completed_event_reports_challenged_of_eligible(self):
        book = material_ledger(self.packet)
        answer = {'critiques': [critique('H001', 'stands', '', ['C:U1'])]}
        with request_budget('detailed'), \
                patch.object(fault, '_ask', return_value=(answer, {})):
            fault.challenge(self.packet, book, key='k', model='m', provider='meta')
        done = next(e for e in book['events'] if e['kind'] == 'challenge_completed')
        self.assertEqual(done['data']['eligible'], 1)
        self.assertIn('of 1 material', done['detail'])


if __name__ == '__main__':
    unittest.main()
