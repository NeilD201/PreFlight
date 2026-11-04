"""Per-finding verification: Gate 1, Gate 2, reinvestigation, consistency.

Offline packet builders mirror test_verdict.py. Model stages are mocked at
the _ask boundary so these tests never touch the network; a pipeline test
proves the production investigate() path actually invokes the gates.
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import design_scan, fault, ledger, reviewer, reverify, verdict
from pcb_checker.ai.usage import request_budget
from test_circuit_audit import fixture


def part(ref, pins=(), dnp=False, mpn=''):
    return {'reference': ref, 'value': '', 'footprint': '', 'mpn': mpn, 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


def packet_for(components, excerpts=()):
    return {'components': components, 'datasheet_excerpts': list(excerpts),
            'review_groups': [], 'design_context': {}}


def excerpt(eid, text, refs=(), mpn=''):
    return {'evidence_id': eid, 'text': text, 'references': list(refs), 'mpn': mpn,
            'part_match': 'assigned', 'page': 1, 'url': '', 'sha256': 'f' * 64}


def supported_lonely(packet):
    """Run discovery + Gate A so H001 is a supported DSG-001 finding."""
    book = ledger.new_ledger({})
    design_scan.discover(packet, book, origin='circuit')
    verdict.verify(packet, book, {})
    return book


def stands_critiques(packet, claims, **_kwargs):
    out = []
    for claim in claims:
        scope = claim.get('scope', {})
        out.append({'claim_id': claim['id'], 'verdict': 'stands', 'counterevidence': '',
                    'cited_ids': [f"C:{r}" for r in scope.get('components', ())]
                    + [f"N:{n}" for n in scope.get('nets', ())],
                    'missing_check': 'Confirm the net export is complete.',
                    'proposed_fix': {'proposed': 'no', 'action': '', 'reference': '', 'pin': '',
                                     'net': '', 'value': '', 'connection': '', 'verify_steps': []}})
    return {'critiques': out}, {}


class Gate1Tests(unittest.TestCase):
    def test_genuine_fault_passes_gate1(self):
        book = supported_lonely(packet_for([part('U1', [('1', 'OUT', 'SOLO')])]))
        record = reverify.gate1(packet_for([part('U1', [('1', 'OUT', 'SOLO')])]), book, 'H001')
        self.assertEqual(record['outcome'], 'pass')
        self.assertTrue(all(c['result'] == 'verified' for c in record['checks']))

    def test_wrong_pin_net_assumption_is_contradiction(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'GND'), ('2', 'IN', 'VCC')])])
        book = ledger.new_ledger({})
        ledger.add_claim(book, origin='circuit', statement='[DSG-001] Lonely net VCC.',
                         scope={'components': ['U1'], 'nets': ['VCC'], 'pins': ['U1.1']},
                         evidence_ids=['C:U1', 'N:VCC'])
        ledger.set_status(book, 'H001', 'under_investigation', reason='test', actor='test')
        ledger.set_status(book, 'H001', 'supported', reason='test', actor='test')
        record = reverify.gate1(pkt, book, 'H001')
        self.assertEqual(record['outcome'], 'fail')
        pin = next(c for c in record['checks'] if c['name'] == 'pin_net')
        self.assertEqual(pin['result'], 'contradiction')
        self.assertIn('GND', pin['reason'])

    def test_missing_evidence_stays_incomplete(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        book['claims'][0]['evidence'].append({'id': 'N:GONE', 'supports': True})
        record = reverify.gate1(pkt, book, 'H001')
        self.assertEqual(record['outcome'], 'incomplete')
        links = next(c for c in record['checks'] if c['name'] == 'links')
        self.assertEqual(links['result'], 'missing')

    def test_irrelevant_citation_is_unverifiable(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])],
                         excerpts=[excerpt('D:C1:p1:0', 'Packaging tape and reel dimensions for shipping.')])
        book = supported_lonely(pkt)
        book['claims'][0]['evidence'].append({'id': 'D:C1:p1:0', 'supports': True})
        record = reverify.gate1(pkt, book, 'H001')
        passage = next(c for c in record['checks'] if c['name'].startswith('passage:'))
        self.assertEqual(passage['result'], 'unverifiable')
        self.assertEqual(record['outcome'], 'incomplete')

    def test_relevant_passage_supports(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])],
                         excerpts=[excerpt('D:C1:p1:0',
                                           'Lonely unconnected net pins must be tied or confirmed single point nets.',
                                           refs=['U1'])])
        book = supported_lonely(pkt)
        book['claims'][0]['evidence'].append({'id': 'D:C1:p1:0', 'supports': True})
        record = reverify.gate1(pkt, book, 'H001')
        passage = next(c for c in record['checks'] if c['name'].startswith('passage:'))
        self.assertEqual(passage['result'], 'verified')

    def test_wrong_datasheet_variant_is_contradiction(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')], mpn='PART-A')],
                         excerpts=[excerpt('D:C1:p1:0', 'Lonely single point net guidance here.',
                                           refs=['U1'], mpn='PART-B')])
        book = supported_lonely(pkt)
        book['claims'][0]['evidence'].append({'id': 'D:C1:p1:0', 'supports': True})
        record = reverify.gate1(pkt, book, 'H001')
        passage = next(c for c in record['checks'] if c['name'].startswith('passage:'))
        self.assertEqual(passage['result'], 'contradiction')
        self.assertEqual(record['outcome'], 'fail')

    def test_broken_calculation_is_contradiction(self):
        rows = [{'key': 'i2c:BUS', 'label': 'BUS', 'status': 'checked',
                 'fields': [{'key': 'r', 'label': 'R', 'unit': 'ohm'}],
                 'values': {'r': float('nan')}, 'detail': 'x', 'metrics': {}}]
        outcomes = verdict.check_calc_rows(rows)
        self.assertEqual(outcomes[0]['result'], 'contradiction')
        rows[0]['values'] = {'r': 4700.0}
        self.assertEqual(verdict.check_calc_rows(rows)[0]['result'], 'verified')
        rows[0]['stale'] = True
        self.assertEqual(verdict.check_calc_rows(rows)[0]['result'], 'missing')

    def test_stale_detector_condition_fails(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        # A second pin joins SOLO: the lonely-net condition is gone.
        grown = packet_for([part('U1', [('1', 'OUT', 'SOLO')]),
                            part('U2', [('1', 'IN', 'SOLO')])])
        record = reverify.gate1(grown, book, 'H001')
        detector = next(c for c in record['checks'] if c['name'] == 'detector')
        self.assertEqual(detector['result'], 'contradiction')


class Gate2Tests(unittest.TestCase):
    def test_judge_verdict_recorded_per_finding(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask', side_effect=stands_critiques):
            reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'stands')

    def test_earlier_challenge_verdict_is_reused(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.record_event(book, 'challenge', 'weakened: export may be partial.',
                            actor='fault', claim_id='H001')
        with patch.object(fault, '_ask') as asked:
            reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        asked.assert_not_called()
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'weakened')

    def test_judge_failure_marks_unavailable(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask', side_effect=reviewer.AIError('provider down')):
            reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'unavailable')
        self.assertTrue(any(e['kind'] == 'fault_unavailable' for e in book['events']))

    def test_validation_failure_is_not_retried(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.record_event(book, 'fault_unavailable', 'The challenge cited unresolvable evidence.',
                            actor='fault')
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask') as asked:
            reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        asked.assert_not_called()
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'unavailable')

    def test_transient_failure_may_reattempt(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.record_event(book, 'fault_unavailable', 'Connection reset.', actor='fault',
                            data={'diagnostics': {'error_kind': 'connection', 'retryable': True}})
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask', side_effect=stands_critiques) as asked:
            reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        asked.assert_called_once()
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'stands')

    def test_budget_exhaustion_skips_remaining(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        with request_budget('economy', 'review') as budget:
            budget.requests = budget.limit
            with patch.object(fault, '_ask') as asked:
                reverify.gate2(pkt, book, ['H001'], key='k', model='m', provider='meta')
        asked.assert_not_called()
        self.assertEqual(book['claims'][0]['judge']['verdict'], 'skipped')


class ReinvestigationTests(unittest.TestCase):
    def test_withdrawn_when_detector_no_longer_triggers(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        grown = packet_for([part('U1', [('1', 'OUT', 'SOLO')]),
                            part('U2', [('1', 'IN', 'SOLO')])])
        revised = reverify.reinvestigate(grown, book, 'H001', ['Detector condition gone.'])
        self.assertTrue(revised)
        self.assertEqual(book['claims'][0]['status'], 'contradicted')
        self.assertEqual(book['claims'][0]['reinvestigations'][0]['action'], 'withdrawn')

    def test_evidence_added_on_partial_claim(self):
        pkt = packet_for([part('U1', [('1', 'SDA', 'BUS')]),
                          part('U2', [('1', 'SDA', 'BUS'), ('2', 'SCL', 'CLK')])])
        book = ledger.new_ledger({})
        # Lonely CLK claim carrying only the component link.
        ledger.add_claim(book, origin='circuit',
                         statement='[DSG-001] Lonely net: Net CLK has a single connected pin.',
                         scope={'components': ['U2'], 'nets': ['CLK'], 'pins': ['U2.2']},
                         evidence_ids=['C:U2'])
        ledger.set_status(book, 'H001', 'under_investigation', reason='test', actor='test')
        ledger.set_status(book, 'H001', 'supported', reason='test', actor='test')
        revised = reverify.reinvestigate(pkt, book, 'H001', ['Links incomplete.'])
        self.assertTrue(revised)
        attempt = book['claims'][0]['reinvestigations'][0]
        self.assertEqual(attempt['action'], 'evidence_added')
        self.assertIn('N:CLK', {e['id'] for e in book['claims'][0]['evidence']})

    def test_no_new_evidence_terminates(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        revised = reverify.reinvestigate(pkt, book, 'H001', ['Recheck.'])
        self.assertFalse(revised)
        self.assertEqual(book['claims'][0]['reinvestigations'][0]['action'], 'no_new_evidence')

    def test_retries_are_bounded(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        for _ in range(reverify.MAX_REINVESTIGATIONS):
            reverify.reinvestigate(pkt, book, 'H001', ['Again.'])
        self.assertFalse(reverify.reinvestigate(pkt, book, 'H001', ['Again.']))
        self.assertTrue(any(e['kind'] == 'reinvest_exhausted' for e in book['events']))
        self.assertEqual(len(book['claims'][0]['reinvestigations']), reverify.MAX_REINVESTIGATIONS)

    def test_unknown_origin_has_no_detector(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = ledger.new_ledger({})
        ledger.add_claim(book, origin='custom', statement='A bespoke worry without a mode.',
                         scope={'components': ['U1'], 'nets': [], 'pins': []})
        revised = reverify.reinvestigate(pkt, book, 'H001', ['Recheck.'])
        self.assertFalse(revised)
        self.assertEqual(book['claims'][0]['reinvestigations'][0]['action'], 'no_rerunnable_detector')


class ConsistencyTests(unittest.TestCase):
    def test_duplicates_are_noted_and_kept(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.add_claim(book, origin='doubt',
                         statement='[DSG-001] Lonely net: SOLO looks unconnected at the far end.',
                         scope={'components': ['U1'], 'nets': ['SOLO'], 'pins': ['U1.1']},
                         evidence_ids=['C:U1', 'N:SOLO'])
        ledger.set_status(book, 'H002', 'under_investigation', reason='test', actor='test')
        ledger.set_status(book, 'H002', 'supported', reason='test', actor='test')
        reopened = reverify.final_review(pkt, book)
        self.assertEqual(reopened, [])
        self.assertTrue(any(e['kind'] == 'duplicate_noted' for e in book['events']))
        self.assertEqual(len(book['claims']), 2)

    def test_supported_vs_contradicted_reopens(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.add_claim(book, origin='doubt',
                         statement='[DSG-001] Lonely net: SOLO has a single connected pin.',
                         scope={'components': ['U1'], 'nets': ['SOLO'], 'pins': ['U1.1']},
                         evidence_ids=['C:U1', 'N:SOLO'])
        ledger.set_status(book, 'H002', 'under_investigation', reason='test', actor='test')
        ledger.set_status(book, 'H002', 'contradicted', reason='test', actor='test')
        reopened = reverify.final_review(pkt, book)
        self.assertEqual(reopened, ['H002'])
        self.assertEqual(book['claims'][1]['status'], 'unresolved')
        self.assertTrue(any(e['kind'] == 'consistency_conflict' for e in book['events']))

    def test_conflicting_fixes_are_flagged(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        ledger.add_claim(book, origin='circuit', statement='[PWR-001] Supply worry on U1.',
                         scope={'components': ['U1'], 'nets': [], 'pins': []},
                         evidence_ids=['C:U1'])
        ledger.set_status(book, 'H002', 'under_investigation', reason='test', actor='test')
        ledger.set_status(book, 'H002', 'supported', reason='test', actor='test')
        book['claims'][0]['proposed_fix'] = {'action': 'add pull-up', 'reference': 'R1', 'pin': '1',
                                             'net': 'SOLO', 'value': '4.7k', 'connection': ''}
        book['claims'][1]['proposed_fix'] = {'action': 'add pull-up', 'reference': 'R1', 'pin': '1',
                                             'net': 'SOLO', 'value': '10k', 'connection': ''}
        reverify.final_review(pkt, book)
        self.assertTrue(any(e['kind'] == 'fix_conflict' for e in book['events']))
        self.assertTrue(any('conflicts' in q for q in book['claims'][0]['open_questions']))


class FinalVerdictTests(unittest.TestCase):
    def _verdict(self, packet, configure):
        book = supported_lonely(packet)
        configure(book['claims'][0])
        counts = reverify.finalize_verdicts(book)
        return book['claims'][0]['final'], counts

    def test_pass_plus_stands_is_verified(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        final, _ = self._verdict(pkt, lambda c: c.update(
            gate1={'outcome': 'pass', 'checks': []}, judge={'verdict': 'stands', 'reason': ''}))
        self.assertEqual(final['verdict'], 'verified')

    def test_weakened_judge_is_unresolved(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        final, _ = self._verdict(pkt, lambda c: c.update(
            gate1={'outcome': 'pass', 'checks': []}, judge={'verdict': 'weakened', 'reason': 'x'}))
        self.assertEqual(final['verdict'], 'unresolved')

    def test_missing_judge_is_unresolved(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        final, _ = self._verdict(pkt, lambda c: c.update(
            gate1={'outcome': 'pass', 'checks': []}, judge={'verdict': 'skipped', 'reason': 'budget'}))
        self.assertEqual(final['verdict'], 'unresolved')

    def test_gate1_failure_rejects_with_reason(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        checks = [{'name': 'detector', 'result': 'contradiction', 'reason': 'Condition gone.'}]
        final, _ = self._verdict(pkt, lambda c: c.update(
            gate1={'outcome': 'fail', 'checks': checks}, judge={'verdict': 'stands', 'reason': ''}))
        self.assertEqual(final['verdict'], 'rejected')
        self.assertIn('Condition gone', final['reason'])

    def test_scope_failure_is_not_applicable(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        checks = [{'name': 'scope', 'result': 'contradiction', 'reason': 'U9 missing.'}]
        book = supported_lonely(pkt)
        book['claims'][0].update(gate1={'outcome': 'fail', 'checks': checks})
        reverify.finalize_verdicts(book)
        self.assertEqual(book['claims'][0]['status'], 'not_applicable')
        self.assertEqual(book['claims'][0]['final']['verdict'], 'rejected')


class VerifyAllTests(unittest.TestCase):
    def test_stale_revision_refuses(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        usage = reverify.verify_all(pkt, book, {'changed': 'yes'}, key='k', model='m',
                                    provider='meta')
        self.assertEqual(usage, {})
        self.assertNotIn('gate1', book['claims'][0])
        self.assertTrue(any(e['kind'] == 'gate1_refused' for e in book['events']))

    def test_full_loop_settles_every_finding(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask', side_effect=stands_critiques):
            usage = reverify.verify_all(pkt, book, {}, key='k', model='m', provider='meta')
        self.assertEqual(usage, {})
        claim = book['claims'][0]
        self.assertEqual(claim['gate1']['outcome'], 'pass')
        self.assertEqual(claim['judge']['verdict'], 'stands')
        self.assertEqual(claim['final']['verdict'], 'verified')
        kinds = {e['kind'] for e in book['events']}
        self.assertTrue({'gate1_recorded', 'challenge_completed', 'verdict_recorded',
                         'final_reviewed', 'verification_completed'} <= kinds)

    def test_failed_findings_reinvestigate_then_reject(self):
        pkt = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])
        book = supported_lonely(pkt)
        grown = packet_for([part('U1', [('1', 'OUT', 'SOLO')]),
                            part('U2', [('1', 'IN', 'SOLO')])])
        with request_budget('detailed', 'review'), \
                patch.object(fault, '_ask', side_effect=stands_critiques):
            reverify.verify_all(grown, book, {}, key='k', model='m', provider='meta')
        claim = book['claims'][0]
        self.assertEqual(claim['status'], 'contradicted')
        self.assertEqual(claim['final']['verdict'], 'rejected')
        self.assertTrue(claim['reinvestigations'])

    def test_production_pipeline_invokes_the_gates(self):
        from pcb_checker.ai import consult, gauge
        from pcb_checker.ai import investigation as workflow
        project = fixture()
        packet = reviewer.evidence_packet(project)

        def plan_value(*_args, **_kwargs):
            return ({'product_hypothesis': 'A sensor board.',
                     'tasks': [{'tool': 'trace_connections', 'references': ['U1'],
                                'question': 'Where does SDA go?',
                                'reason': 'Follow the interface end to end.'}]}, {})

        def consults(_packet, _ledger, questions, **_kwargs):
            return ({'consultations': [
                {'claim_id': cid, 'question': q, 'finding': 'answered',
                 'answer': 'The trace reaches the controller pin.', 'cited_ids': []}
                for cid, q in questions]}, {})

        def audits(_packet, claims, **_kwargs):
            return ({'audits': [
                {'claim_id': c['id'], 'sureness': '99', 'reasoning': 'Evidence supports it.',
                 'disagreements': [], 'cited_ids': []} for c in claims]}, {})

        with request_budget('detailed', 'review'), \
                patch.object(workflow, 'ask_plan', side_effect=plan_value), \
                patch.object(fault, '_ask', side_effect=stands_critiques), \
                patch.object(consult, '_ask', side_effect=consults), \
                patch.object(gauge, '_ask', side_effect=audits):
            _expanded, trace, _usage = workflow.investigate(
                project, packet, key='fake', model='test', provider='meta')
        kinds = {e['kind'] for e in trace['ledger']['events']}
        self.assertTrue({'gate1_recorded', 'verdict_recorded', 'verification_completed'} <= kinds)
        self.assertTrue(all('final' in c for c in trace['ledger']['claims']
                            if c.get('status') in ('supported', 'unresolved', 'contradicted',
                                                   'not_applicable')))
        from pcb_checker.ai.replay import coverage_proof
        proof = coverage_proof(trace)
        self.assertIn('verification', proof)
        self.assertTrue(any(v['id'] == c['id'] and v['final'] == (c.get('final') or {}).get('verdict')
                            for v in proof['verification'] for c in trace['ledger']['claims']
                            if c.get('status') in ('supported', 'unresolved')) or
                        not [c for c in trace['ledger']['claims']
                             if c.get('status') in ('supported', 'unresolved')])
        self.assertNotIn('fake', __import__('json').dumps(trace))


if __name__ == '__main__':
    unittest.main()
