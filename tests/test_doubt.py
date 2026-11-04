"""DOUBT blind pass: gap detection without prior conclusions, verified re-entry."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import atlas, design_scan, doubt, ledger, reviewer, scenario_scan, verdict
from test_circuit_audit import fixture


def packet_for(components):
    return {'components': components}


def part(ref, value='', pins=(), dnp=False):
    return {'reference': ref, 'value': value, 'footprint': '', 'mpn': '', 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


def examined_ledger(refs, nets=()):
    book = ledger.new_ledger({})
    for index, ref in enumerate(refs):
        ledger.add_claim(book, origin='scope', statement=f'Claim {index} about {ref}.',
                         scope={'components': [ref], 'nets': [], 'pins': []})
    if nets:
        ledger.add_claim(book, origin='scope', statement='Net claim.',
                         scope={'components': [], 'nets': list(nets), 'pins': []})
    return book


class GapTests(unittest.TestCase):
    def test_unexamined_active_parts_and_nets_found(self):
        packet = packet_for([
            part('U1', 'MCU', [('1', 'VDD', 'VCC')]), part('U2', 'SENSOR', [('1', 'VDD', 'VCC')]),
            part('J1', 'CONN', [('1', 'S', 'SIG')]), part('R1', '10k', [('1', '~', 'VCC')])])
        gaps = doubt.find_gaps(packet, examined_ledger(['U1'], ['VCC']))
        self.assertEqual(gaps['unexamined_claimable'], ['J1', 'U2'])
        self.assertEqual(gaps['unexamined_other'], ['R1'])
        self.assertEqual(gaps['unexamined_nets'], ['SIG'])

    def test_dnp_parts_never_count_as_gaps(self):
        packet = packet_for([part('U9', 'X', dnp=True)])
        gaps = doubt.find_gaps(packet, ledger.new_ledger({}))
        self.assertEqual(gaps['unexamined_claimable'], [])

    def test_tool_references_count_as_examined(self):
        packet = packet_for([part('U1', 'X', [('1', 'A', 'N1')])])
        book = ledger.new_ledger({})
        ledger.record_event(book, 'tool_executed', 'did', actor='vector',
                            data={'references': ['U1']})
        gaps = doubt.find_gaps(packet, book)
        self.assertEqual(gaps['unexamined_claimable'], [])

    def test_blindness_to_statements_and_statuses(self):
        packet = packet_for([part('U1', 'X', [('1', 'A', 'N1')]), part('U2', 'Y')])
        first = doubt.find_gaps(packet, examined_ledger(['U1']))
        book = examined_ledger(['U1'])
        for claim in book['claims']:
            claim['statement'] = 'Scrubbed conclusion with 99% sureness.'
            claim['status'] = 'supported'
            claim['agent_sureness'] = 99
        second = doubt.find_gaps(packet, book)
        self.assertEqual(first, second)
        self.assertEqual(first['unexamined_claimable'], ['U2'])


class AssessTests(unittest.TestCase):
    def test_new_concerns_recorded_and_reverified(self):
        packet = packet_for([
            part('U1', 'MCU', [('1', 'VDD', 'VCC')]), part('U2', 'SENSOR', [('1', 'VDD', 'VCC')])])
        book = examined_ledger(['U1'], ['VCC'])
        summary = doubt.assess(packet, book)
        self.assertEqual(len(summary['claims']), 1)
        claim = next(c for c in book['claims'] if c['origin'] == 'doubt')
        self.assertIn('[DBT-001]', claim['statement'])
        self.assertEqual(claim['scope']['components'], ['U2'])
        self.assertEqual(claim['status'], 'hypothesized')
        results = verdict.verify(packet, book, {})
        self.assertEqual(results['verified'], 3)
        self.assertEqual(claim['status'], 'unresolved')
        self.assertTrue(claim['open_questions'])
        event = next(e for e in book['events'] if e['kind'] == 'doubt_assessed')
        self.assertEqual(event['data']['gaps']['unexamined_claimable'], ['U2'])
        self.assertNotIn('secret', json.dumps(book))

    def test_quiet_pass_still_records_gaps(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        atlas.discover(packet, book)
        design_scan.discover(packet, book)
        scenario_scan.discover(packet, book)
        summary = doubt.assess(packet, book)
        self.assertEqual(summary['claims'], [])
        event = next(e for e in book['events'] if e['kind'] == 'doubt_assessed')
        self.assertEqual(event['data']['gaps']['unexamined_other'], ['R1'])
        self.assertEqual(event['data']['gaps']['unexamined_nets'], [])

    def test_recording_budget_reports_truncation(self):
        packet = packet_for([part(f'U{i}', 'X') for i in range(1, 5)])
        book = ledger.new_ledger({})
        summary = doubt.assess(packet, book, limit=2)
        self.assertEqual(len(summary['claims']), 2)
        self.assertEqual(summary['truncated'], 2)
        self.assertTrue(any('budget' in limitation for limitation in book['limitations']))


if __name__ == '__main__':
    unittest.main()
