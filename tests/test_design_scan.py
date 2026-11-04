"""Design-driven discovery: structure checks, findings bridge, stream independence."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import atlas, design_scan, ledger, reviewer
from test_circuit_audit import fixture


def packet_for(components, groups=()):
    return {'components': components, 'review_groups': list(groups)}


def part(ref, pins=(), dnp=False):
    return {'reference': ref, 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net, 'no_connect': nc}
                     for n, name, net, nc in pins]}


class StructureTests(unittest.TestCase):
    def test_lonely_net_claims_but_nc_and_dnp_stay_out(self):
        data = design_scan.facts(packet_for([
            part('U1', [('1', 'OUT', 'SOLO', False)]),
            part('U2', [('1', 'NC', 'FLOAT', True)]),
            part('U3', [('1', 'X', 'SKIP', False)], dnp=True)]))
        findings, notes = design_scan.lonely_nets(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['standing'], 'applicable')
        self.assertEqual(findings[0]['scope']['pins'], ['U1.1'])
        self.assertEqual(len(notes), 2)

    def test_hub_threshold_counts_pins(self):
        pins = [(str(i), 'P', 'BIG', False) for i in range(1, 7)]
        data = design_scan.facts(packet_for([part('U1', pins)]))
        findings, _ = design_scan.hub_nets(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['standing'], 'uncertain')
        data = design_scan.facts(packet_for([part('U1', pins[:5])]))
        self.assertEqual(design_scan.hub_nets(data)[0], [])

    def test_unpaired_pair_fires_only_without_mate(self):
        data = design_scan.facts(packet_for([part('U1', [('1', 'USB_DP', 'D1', False)])]))
        findings, _ = design_scan.unpaired_signals(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('USB_DM', findings[0]['basis'])
        data = design_scan.facts(packet_for([part('U1', [('1', 'USB_DP', 'D1', False), ('2', 'USB_DM', 'D2', False)])]))
        self.assertEqual(design_scan.unpaired_signals(data)[0], [])

    def test_supply_names_never_read_as_pairs(self):
        data = design_scan.facts(packet_for([
            part('U1', [('1', 'VIN', 'VIN', False), ('2', 'VCC', 'VCC', False), ('3', 'GND', 'GND', False)])]))
        self.assertEqual(design_scan.unpaired_signals(data)[0], [])

    def test_crystal_needs_both_ends_on_same_part(self):
        data = design_scan.facts(packet_for([part('U1', [('1', 'XI', 'X1', False)])]))
        findings, _ = design_scan.unpaired_signals(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['standing'], 'applicable')
        data = design_scan.facts(packet_for(
            [part('U1', [('1', 'XI', 'X1', False), ('2', 'XO', 'X2', False)])]))
        self.assertEqual(design_scan.unpaired_signals(data)[0], [])

    def test_subsystem_partition_skips_dnp(self):
        data = design_scan.facts(packet_for([
            part('U1', [('1', 'A', 'N1', False)]), part('U2', [('1', 'A', 'N1', False)]),
            part('U3', [('1', 'A', 'N2', False)]), part('U4', [('1', 'A', 'N2', False)], dnp=True)]))
        self.assertEqual(design_scan.subsystems(data), [['U1', 'U2'], ['U3']])


class FindingsBridgeTests(unittest.TestCase):
    def test_fix_and_review_groups_become_claims(self):
        groups = [{'id': 'R001', 'priority': 'fix', 'title': 'Short', 'summary': 'desc', 'components': ['U1']},
                  {'id': 'R002', 'priority': 'review', 'title': 'Check', 'summary': '', 'components': ['U2']},
                  {'id': 'R003', 'priority': 'note', 'title': 'FYI', 'components': []}]
        findings, notes = design_scan.review_findings(design_scan.facts(packet_for([], groups)))
        self.assertEqual(len(findings), 2)
        self.assertIn('R001', findings[0]['basis'])
        self.assertEqual(findings[0]['scope']['components'], ['U1'])
        self.assertEqual(len(notes), 1)

    def test_empty_groups_noted(self):
        self.assertEqual(design_scan.review_findings(design_scan.facts(packet_for([])))[0], [])


class DiscoveryTests(unittest.TestCase):
    def test_fixture_discovery_maps_structure_and_subsystems(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = design_scan.discover(packet, book)
        self.assertEqual(summary['checks'], 4)
        self.assertIn('DSG-001', summary['triggered'])
        self.assertIn('DSG-004', summary['excluded'])
        self.assertEqual(summary['subsystems'], [['R1', 'U1', 'U2']])
        claims = {c['id']: c for c in book['claims']}
        self.assertEqual(set(summary['claims']), set(claims))
        lonely = [c for c in claims.values() if '[DSG-001]' in c['statement']]
        self.assertEqual(len(lonely), 1)
        self.assertEqual(lonely[0]['scope']['nets'], ['GND'])
        self.assertEqual(lonely[0]['origin'], 'circuit')
        event = next(e for e in book['events'] if e['kind'] == 'design_assessed')
        self.assertEqual(event['data']['subsystems'], [['R1', 'U1', 'U2']])
        self.assertNotIn('secret', json.dumps(book))

    def test_streams_stay_independent_until_merge(self):
        packet = reviewer.evidence_packet(fixture())
        solo = ledger.new_ledger({})
        design_scan.discover(packet, solo)
        solo_statements = sorted(c['statement'] for c in solo['claims'])
        merged = ledger.new_ledger({})
        atlas.discover(packet, merged, origin='scope')
        design_scan.discover(packet, merged, origin='circuit')
        circuit_statements = sorted(c['statement'] for c in merged['claims'] if c['origin'] == 'circuit')
        self.assertEqual(solo_statements, circuit_statements)
        self.assertGreater(solo_statements, [])

    def test_recording_budget_reports_truncation(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = design_scan.discover(packet, book, limit=0)
        self.assertEqual(summary['claims'], [])
        self.assertGreater(summary['truncated'], 0)
        self.assertTrue(any('budget' in limitation for limitation in book['limitations']))


class RealBoardRegressionTests(unittest.TestCase):
    def test_hub_skips_rails_but_fires_on_signal_bus(self):
        rail = [(str(i), 'VDD', '+3.3V', False) for i in range(1, 7)]
        data = design_scan.facts(packet_for([part('U1', rail)]))
        findings, notes = design_scan.hub_nets(data)
        self.assertEqual(findings, [])
        self.assertTrue(notes)
        bus = [(str(i), 'SDA', 'SDA', False) for i in range(1, 7)]
        data = design_scan.facts(packet_for([part('U1', bus)]))
        self.assertEqual(len(design_scan.hub_nets(data)[0]), 1)

    def test_pin_polarity_names_are_not_pairs_but_net_mates_are(self):
        data = design_scan.facts(packet_for(
            [part('U1', [('1', 'VLED+', '+3.3V', False)])]))
        self.assertEqual(design_scan.unpaired_signals(data)[0], [])
        data = design_scan.facts(packet_for(
            [part('U1', [('1', 'A', 'VLED+', False)])]))
        findings, _ = design_scan.unpaired_signals(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('VLED-', findings[0]['basis'])


if __name__ == '__main__':
    unittest.main()
