"""Gate A finding verification: scope, links, re-checks and revision refusal."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import atlas, design_scan, ledger, reviewer, scenario_scan, verdict
from test_circuit_audit import fixture


def packet_for(components, excerpts=()):
    return {'components': components, 'datasheet_excerpts': list(excerpts)}


def part(ref, pins=(), dnp=False):
    return {'reference': ref, 'value': '', 'footprint': '', 'mpn': '', 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


def claim(statement, scope=None, evidence=()):
    return {'statement': statement, 'scope': scope or {'components': [], 'nets': [], 'pins': []},
            'evidence': [{'id': e, 'supports': True} for e in evidence]}


SOLO = packet_for([part('U1', [('1', 'OUT', 'SOLO')])])


class ScopeTests(unittest.TestCase):
    def test_absent_objects_are_not_applicable_with_reasons(self):
        data = verdict.gate_facts(SOLO)
        cases = [({'components': ['U9']}, 'U9 does not exist'),
                 ({'nets': ['GONE']}, 'Net GONE does not exist'),
                 ({'pins': ['U1.9']}, 'Pin U1.9 does not exist'),
                 ({'pins': ['broken']}, 'Pin broken does not exist')]
        for scope, reason in cases:
            ok, detail = verdict.check_scope(data, claim('s', scope))
            self.assertFalse(ok)
            self.assertIn(reason, detail)
        data = verdict.gate_facts(packet_for([part('U1', [('1', 'OUT', 'SOLO')], dnp=True)]))
        ok, detail = verdict.check_scope(data, claim('s', {'components': ['U1']}))
        self.assertFalse(ok)
        self.assertIn('DNP', detail)
        ok, _ = verdict.check_scope(verdict.gate_facts(SOLO),
                                    claim('s', {'components': ['U1'], 'nets': ['SOLO'], 'pins': ['U1.1']}))
        self.assertTrue(ok)

    def test_unresolvable_links_stay_unresolved(self):
        data = verdict.gate_facts(SOLO)
        ok, detail = verdict.check_links(data, claim('s', {}, ['N:MISSING', 'X:1']))
        self.assertFalse(ok)
        self.assertIn('N:MISSING', detail)
        self.assertIn('X:1', detail)
        data = verdict.gate_facts(packet_for(
            [part('U1', [('1', 'OUT', 'SOLO')])], excerpts=[{'evidence_id': 'D:C1:p1:0'}]))
        ok, _ = verdict.check_links(data, claim('s', {}, ['C:U1', 'N:SOLO', 'D:C1:p1:0']))
        self.assertTrue(ok)
        ok, _ = verdict.check_links(data, claim('s', {}, ['D:unknown']))
        self.assertFalse(ok)


class RecheckTests(unittest.TestCase):
    def test_lonely_net_supported(self):
        book = ledger.new_ledger({})
        design_scan.discover(SOLO, book)
        results = verdict.verify(SOLO, book, {})
        self.assertEqual(results['supported'], 1)
        self.assertEqual(book['claims'][0]['status'], 'supported')
        self.assertEqual(book['claims'][0]['verification'], 'deterministic')

    def test_stale_conditions_are_contradicted(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        ledger.add_claim(book, origin='test',
                         statement='[DSG-001] Lonely net: Net BUS has a single connected pin.',
                         scope={'components': [], 'nets': ['BUS'], 'pins': []}, evidence_ids=['N:BUS'])
        ledger.add_claim(book, origin='test',
                         statement='[IF-001] I2C pull-ups: I2C net BUS has no visible pull-up resistor.',
                         scope={'components': ['U1', 'U2'], 'nets': ['BUS'],
                                'pins': ['U1.2', 'U2.1']}, evidence_ids=['N:BUS'])
        ledger.add_claim(book, origin='test',
                         statement='[MFG-001] Part identity: U1 has no part identity.',
                         scope={'components': ['U1'], 'nets': [], 'pins': []}, evidence_ids=['C:U1'])
        results = verdict.verify(packet, book, {})
        self.assertEqual(results['contradicted'], 3)
        self.assertTrue(all(c['status'] == 'contradicted' for c in book['claims']))

    def test_judgment_modes_stay_unresolved_with_questions(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        atlas.discover(packet, book)
        scenario_scan.discover(packet, book)
        verdict.verify(packet, book, {})
        by_status = {}
        for c in book['claims']:
            by_status.setdefault(c['status'], []).append(c['statement'][:9])
        self.assertIn('[IF-002] ', by_status['unresolved'])
        self.assertIn('[SCN-001]', by_status['unresolved'])
        unresolved = [c for c in book['claims'] if c['status'] == 'unresolved']
        self.assertTrue(all(c['open_questions'] for c in unresolved))

    def test_claims_without_mode_ids_need_specialists(self):
        book = ledger.new_ledger({})
        ledger.add_claim(book, origin='vector_plan', statement='A sensor board.',
                         scope={'components': ['U1']}, evidence_ids=['C:U1'])
        results = verdict.verify(SOLO, book, {})
        self.assertEqual(results['unresolved'], 1)


class VerifyFlowTests(unittest.TestCase):
    def test_stale_revision_refuses_everything(self):
        book = ledger.new_ledger({'a': 'b'})
        design_scan.discover(SOLO, book)
        results = verdict.verify(SOLO, book, {'a': 'changed'})
        self.assertEqual(results['verified'], 0)
        self.assertTrue(all(c['status'] == 'hypothesized' for c in book['claims']))
        self.assertTrue(any(e['kind'] == 'gate_a_refused' for e in book['events']))

    def test_settled_claims_are_skipped(self):
        book = ledger.new_ledger({})
        first = ledger.add_claim(book, origin='t', statement='s1', scope={'components': ['U1']},
                                 evidence_ids=['C:U1'])
        ledger.set_status(book, first, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, first, 'unresolved', reason='r', actor='t')
        ledger.add_claim(book, origin='t', statement='[DSG-001] Lonely net: Net SOLO single.',
                         scope={'components': ['U1'], 'nets': ['SOLO'], 'pins': ['U1.1']},
                         evidence_ids=['C:U1', 'N:SOLO'])
        results = verdict.verify(SOLO, book, {})
        self.assertEqual(results['skipped'], 1)
        self.assertEqual(results['supported'], 1)

    def test_empty_ledger_reports_zero_summary(self):
        book = ledger.new_ledger({})
        results = verdict.verify(SOLO, book, {})
        self.assertEqual(results['verified'], 0)
        event = next(e for e in book['events'] if e['kind'] == 'gate_a_completed')
        self.assertEqual(event['data']['verified'], 0)

    def test_fixture_end_to_end_status_map(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        atlas.discover(packet, book)
        design_scan.discover(packet, book)
        scenario_scan.discover(packet, book)
        results = verdict.verify(packet, book, {})
        mapping = {}
        for c in book['claims']:
            mapping.setdefault(c['status'], []).append(c['statement'][:9])
        self.assertEqual(sorted(mapping['supported']),
                         ['[DSG-001]', '[MFG-001]', '[PWR-001]', '[PWR-001]', '[PWR-003]'])
        self.assertEqual(sorted(mapping['unresolved']), ['[IF-002] ', '[SCN-001]'])
        self.assertEqual(results['verified'], 7)
        self.assertNotIn('secret', json.dumps(book))


class GateBFixTests(unittest.TestCase):
    def setUp(self):
        self.packet = packet_for([part('U1', [('1', 'VDD', 'VCC'), ('2', 'SDA', 'BUS')]),
                                  part('U2', [('1', 'IO', 'BUS')])])

    def ledger_with_fix(self, scope, fix):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='scope', statement='[PWR-001] s.',
                               scope=scope, evidence_ids=['C:U1'])
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, 'supported', reason='r', actor='t')
        next(c for c in book['claims'] if c['id'] == cid)['proposed_fix'] = fix
        return book, cid

    def fix(self, **overrides):
        data = {'action': 'Fit cap', 'reference': 'U1', 'pin': '1', 'net': 'VCC',
                'value': '100nF', 'connection': 'Across supply.', 'verify_steps': ['Measure.']}
        data.update(overrides)
        return data

    def test_complete_fix_is_structure_ok_with_value_caveat(self):
        book, cid = self.ledger_with_fix({'components': ['U1'], 'nets': [], 'pins': []}, self.fix())
        self.assertEqual(verdict.verify_fix(self.packet, book, cid), 'structure_ok')
        claim = next(c for c in book['claims'] if c['id'] == cid)
        self.assertTrue(any('not approved' in q for q in claim['fix_gate']['retest']))
        self.assertIn('Measure.', claim['fix_gate']['retest'])
        self.assertTrue(any(r.startswith('Hypothetical') for r in claim['fix_gate']['reasons']))
        self.assertTrue(any(e['kind'] == 'fix_verified' for e in book['events']))

    def test_absent_objects_reject(self):
        cases = [self.fix(reference='U9'), self.fix(pin='9'), self.fix(net='NOPE'),
                 self.fix(action='  '),
                 self.fix(reference='U2')]
        packet = packet_for([part('U1', [('1', 'VDD', 'VCC')]), part('U2', [('1', 'IO', 'BUS')], dnp=True)])
        for fix in cases[:4]:
            book, cid = self.ledger_with_fix({'components': ['U1'], 'nets': [], 'pins': []}, fix)
            with self.subTest(fix=fix):
                self.assertEqual(verdict.verify_fix(self.packet, book, cid), 'rejected')
        book, cid = self.ledger_with_fix({'components': ['U2'], 'nets': [], 'pins': []}, cases[4])
        self.assertEqual(verdict.verify_fix(packet, book, cid), 'rejected')

    def test_gaps_and_overlap_need_testing(self):
        for fix in (self.fix(pin=''), self.fix(net=''), self.fix(value='')):
            book, cid = self.ledger_with_fix({'components': ['U1'], 'nets': [], 'pins': []}, fix)
            with self.subTest(fix=fix):
                self.assertEqual(verdict.verify_fix(self.packet, book, cid), 'needs_testing')
        book, cid = self.ledger_with_fix({'components': ['U2'], 'nets': [], 'pins': []}, self.fix())
        other = ledger.add_claim(book, origin='scope', statement='[PWR-003] s.',
                                 scope={'components': [], 'nets': ['VCC'], 'pins': []})
        ledger.set_status(book, other, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, other, 'supported', reason='r', actor='t')
        self.assertEqual(verdict.verify_fix(self.packet, book, cid), 'needs_testing')
        claim = next(c for c in book['claims'] if c['id'] == cid)
        self.assertTrue(any('overlaps' in r for r in claim['fix_gate']['reasons']))

    def test_missing_fix_or_claim_is_noop(self):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='t', statement='s.', scope={'components': []})
        self.assertIsNone(verdict.verify_fix(self.packet, book, cid))
        self.assertIsNone(verdict.verify_fix(self.packet, book, 'H999'))
        self.assertFalse(any(e['kind'] == 'fix_verified' for e in book['events']))


class ChallengeVerdictTests(unittest.TestCase):
    def setUp(self):
        self.packet = reviewer.evidence_packet(fixture())

    def ledger_with(self, status):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='scope', statement='[IF-002] timing.',
                               scope={'components': ['U1'], 'nets': ['BUS'], 'pins': []},
                               evidence_ids=['C:U1', 'N:BUS'])
        ledger.set_status(book, cid, 'under_investigation', reason='r', actor='t')
        ledger.set_status(book, cid, status, reason='r', actor='t')
        return book, cid

    def test_refuted_with_evidence_closes_unresolved_only(self):
        critique = {'verdict': 'refuted', 'counterevidence': 'Pull-up confirmed by measurement.',
                    'cited_ids': ['C:U1']}
        book, cid = self.ledger_with('unresolved')
        self.assertEqual(verdict.apply_challenge_verdict(self.packet, book, cid, critique), 'contradicted')
        claim = next(c for c in book['claims'] if c['id'] == cid)
        self.assertEqual(claim['status'], 'contradicted')
        status_events = [e for e in book['events'] if e['kind'] == 'status' and e['claim_id'] == cid]
        self.assertEqual(status_events[-1]['actor'], 'verdict')
        self.assertIn('Pull-up confirmed', status_events[-1]['detail'])

    def test_model_opinion_never_moves_other_cases(self):
        good = {'verdict': 'refuted', 'counterevidence': 'Evidence here.', 'cited_ids': ['C:U1']}
        book, cid = self.ledger_with('supported')
        self.assertIsNone(verdict.apply_challenge_verdict(self.packet, book, cid, good))
        self.assertEqual(next(c for c in book['claims'] if c['id'] == cid)['status'], 'supported')
        for critique in ({'verdict': 'weakened', 'counterevidence': 'x.', 'cited_ids': ['C:U1']},
                         {'verdict': 'stands', 'counterevidence': '', 'cited_ids': []},
                         {'verdict': 'refuted', 'counterevidence': '  ', 'cited_ids': ['C:U1']},
                         {'verdict': 'refuted', 'counterevidence': 'x.', 'cited_ids': ['N:NOPE']}):
            book, cid = self.ledger_with('unresolved')
            with self.subTest(critique=critique):
                self.assertIsNone(verdict.apply_challenge_verdict(self.packet, book, cid, critique))
                self.assertEqual(next(c for c in book['claims'] if c['id'] == cid)['status'], 'unresolved')
        book, _ = self.ledger_with('unresolved')
        self.assertIsNone(verdict.apply_challenge_verdict(self.packet, book, 'H999', good))

    def test_hypothesized_claims_wait_for_gate_a(self):
        book = ledger.new_ledger({})
        cid = ledger.add_claim(book, origin='scope', statement='[IF-002] timing.',
                               scope={'components': ['U1'], 'nets': [], 'pins': []})
        critique = {'verdict': 'refuted', 'counterevidence': 'Evidence.', 'cited_ids': ['C:U1']}
        self.assertIsNone(verdict.apply_challenge_verdict(self.packet, book, cid, critique))


if __name__ == '__main__':
    unittest.main()
