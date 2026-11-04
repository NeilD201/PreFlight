"""Failure Atlas: registry, applicability, exclusions and ledger discovery."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import atlas, ledger, reviewer
from test_circuit_audit import fixture


def packet_for(components, coverage=()):
    return {'components': components, 'nets': [],
            'datasheet_coverage': list(coverage),
            'bus_candidates': [], 'bypass_capacitors': []}


def part(ref, value='', pins=(), mpn='', dnp=False):
    return {'reference': ref, 'value': value, 'footprint': '', 'mpn': mpn, 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


class AtlasRegistryTests(unittest.TestCase):
    def test_seed_modes_have_unique_ids_and_required_fields(self):
        seen = set()
        for mode in atlas.modes():
            self.assertNotIn(mode['id'], seen)
            seen.add(mode['id'])
            for key in ('id', 'category', 'subcategory', 'title', 'verify', 'assess', 'methods'):
                self.assertIn(key, mode)
            self.assertTrue(callable(mode['assess']))
        self.assertGreaterEqual(len(seen), 20)
        self.assertGreaterEqual(len({m['category'] for m in atlas.modes()}), 8)

    def test_extensions_register_and_validate(self):
        entry = {'id': 'TEST-001', 'category': 'test', 'title': 't',
                 'assess': lambda facts: ([], ['nothing observed'])}
        try:
            atlas.register(entry)
            self.assertIn('TEST-001', [m['id'] for m in atlas.modes()])
            with self.assertRaises(ValueError):
                atlas.register(dict(entry))
            with self.assertRaises(ValueError):
                atlas.register({'id': 'TEST-002'})
        finally:
            atlas._MODES[:] = [m for m in atlas._MODES if m['id'] != 'TEST-001']

    def test_ohms_parsing(self):
        self.assertEqual(atlas._ohms('4.7k'), 4700)
        self.assertEqual(atlas._ohms('4k7'), 4007)
        self.assertEqual(atlas._ohms('1M'), 1e6)
        self.assertEqual(atlas._ohms('10'), 10)
        self.assertIsNone(atlas._ohms('DNP'))
        self.assertIsNone(atlas._ohms(''))


class ApplicabilityTests(unittest.TestCase):
    def test_facts_derive_net_membership_from_pins(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'X', [('1', 'SDA', 'BUS')]), part('R1', '4.7k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]))
        self.assertEqual({m['ref'] for m in facts['nets']['BUS']}, {'U1', 'R1'})
        self.assertEqual(facts['components']['R1']['nets'], ['BUS', 'VCC'])

    def test_missing_pullup_is_applicable_present_pullup_is_excluded(self):
        bare = atlas.design_facts(packet_for([part('U1', 'X', [('2', 'SDA', 'BUS')])]))
        findings, notes = atlas._assess_i2c_pullups(bare)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['standing'], 'applicable')
        self.assertEqual(findings[0]['scope']['nets'], ['BUS'])
        pulled = atlas.design_facts(packet_for([
            part('U1', 'X', [('2', 'SDA', 'BUS')]),
            part('R1', '4.7k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]))
        findings, notes = atlas._assess_i2c_pullups(pulled)
        self.assertEqual(findings, [])
        self.assertTrue(any('4700' in n for n in notes))

    def test_timing_claim_needs_observed_pullup(self):
        bare = atlas.design_facts(packet_for([part('U1', 'X', [('2', 'SDA', 'BUS')])]))
        self.assertEqual(atlas._assess_i2c_timing(bare)[0], [])
        pulled = atlas.design_facts(packet_for([
            part('U1', 'X', [('2', 'SDA', 'BUS')]),
            part('R1', '10k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]))
        findings, _ = atlas._assess_i2c_timing(pulled)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['standing'], 'uncertain')

    def test_decoupling_checks_each_ic_supply_pin(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'MCU', [('1', 'VDD', 'VCC')]),
            part('C1', '100n', [('1', '~', 'VCC'), ('2', '~', 'GND')])]))
        findings, notes = atlas._assess_power_decoupling(facts)
        self.assertEqual(findings, [])
        self.assertTrue(any('C1' in n for n in notes))
        facts = atlas.design_facts(packet_for([part('U1', 'MCU', [('1', 'VDD', 'VCC')])]))
        findings, _ = atlas._assess_power_decoupling(facts)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['scope']['pins'], ['U1.1'])

    def test_part_identity_prefers_matched_documents(self):
        facts = atlas.design_facts(
            packet_for([part('U1', 'X'), part('U2', 'Y', mpn='ABC123'), part('U3', 'Z')],
                       coverage=[{'reference': 'U1', 'status': 'ready', 'code': 'C1'}]))
        findings, notes = atlas._assess_part_identity(facts)
        scoped = {f['scope']['components'][0] for f in findings}
        self.assertEqual(scoped, {'U2', 'U3'})
        self.assertTrue(any('C1' in n for n in notes))

    def test_floating_pin_and_reset_and_crystal_triggers(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'MCU', [('1', 'IN', ''), ('2', 'NRST', 'RST'), ('3', 'XI', 'X1'), ('4', 'XO', 'X2')])]))
        self.assertEqual(len(atlas._assess_floating_input(facts)[0]), 1)
        self.assertEqual(len(atlas._assess_reset(facts)[0]), 1)
        self.assertEqual(len(atlas._assess_crystal_load(facts)[0]), 1)


class DiscoveryTests(unittest.TestCase):
    def test_fixture_discovery_records_scoped_claims_and_exclusions(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger(packet.get('provenance', {}).get('source_hashes', {}))
        summary = atlas.discover(packet, book)
        self.assertEqual(summary['modes'], len(atlas.modes()))
        self.assertIn('PWR-001', summary['triggered'])
        self.assertIn('PWR-003', summary['triggered'])
        self.assertIn('IF-002', summary['triggered'])
        self.assertIn('MFG-001', summary['triggered'])
        self.assertIn('IF-001', summary['excluded'])
        self.assertIn('PRT-002', summary['excluded'])
        claims = {c['id']: c for c in book['claims']}
        self.assertEqual(set(summary['claims']), set(claims))
        for claim in claims.values():
            self.assertEqual(claim['origin'], 'scope')
            self.assertEqual(claim['status'], ledger.HYPOTHESIZED)
            self.assertRegex(claim['statement'], r'^\[(PWR|IF|MFG)-')
            self.assertTrue(claim['open_questions'])
            self.assertTrue(any(e.startswith(('C:', 'N:')) for e in (l['id'] for l in claim['evidence'])))
        u2 = [c for c in claims.values() if '[MFG-001]' in c['statement']]
        self.assertEqual(len(u2), 1)
        self.assertEqual(u2[0]['scope']['components'], ['U2'])
        event = next(e for e in book['events'] if e['kind'] == 'atlas_assessed')
        self.assertIn('R1', ' '.join(event['data']['notes']['IF-001']))
        self.assertNotIn('secret', json.dumps(book))

    def test_repeat_discovery_merges_without_new_claims(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        first = atlas.discover(packet, book)
        count = len(book['claims'])
        second = atlas.discover(packet, book)
        self.assertEqual(len(book['claims']), count)
        self.assertEqual(first['claims'], second['claims'])
        self.assertTrue(any(e['kind'] == 'merged' for e in book['events']))

    def test_recording_budget_reports_truncation(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = atlas.discover(packet, book, limit=2)
        self.assertEqual(len(summary['claims']), 2)
        self.assertGreater(summary['truncated'], 0)
        self.assertTrue(any('budget' in limitation for limitation in book['limitations']))


class RealBoardRegressionTests(unittest.TestCase):
    def test_pullup_to_dotted_rail_is_recognized(self):
        pulled = atlas.design_facts(packet_for([
            part('U1', 'X', [('2', 'SDA', 'SDA')]),
            part('R1', '4.7k', [('1', '~', 'SDA'), ('2', '~', '+3.3V')])]))
        findings, notes = atlas._assess_i2c_pullups(pulled)
        self.assertEqual(findings, [])
        self.assertTrue(any('R1' in n for n in notes))

    def test_synthetic_unconnected_pin_fires_unless_flagged_or_nc(self):
        open_pin = atlas.design_facts({'components': [
            {'reference': 'U1', 'value': 'MCU', 'footprint': '', 'mpn': '', 'dnp': False,
             'pins': [{'number': '1', 'name': 'P0', 'net': 'unconnected-(U1-P0-Pad1)'}]},
            ], 'nets': [], 'datasheet_coverage': [], 'bus_candidates': [], 'bypass_capacitors': []})
        findings, _ = atlas._assess_floating_input(open_pin)
        self.assertEqual(len(findings), 1)
        flagged = atlas.design_facts({'components': [
            {'reference': 'U1', 'value': 'MCU', 'footprint': '', 'mpn': '', 'dnp': False,
             'pins': [{'number': '1', 'name': 'P0', 'net': 'unconnected-(U1-P0-Pad1)', 'no_connect': True},
                      {'number': '2', 'name': 'NC', 'net': 'unconnected-(U1-NC-Pad2)'}]},
            ], 'nets': [], 'datasheet_coverage': [], 'bus_candidates': [], 'bypass_capacitors': []})
        findings, notes = atlas._assess_floating_input(flagged)
        self.assertEqual(findings, [])
        self.assertEqual(len(notes), 2)

    def test_usb_unconnected_reports_unused_not_impedance(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'MCU', [('1', 'D+', 'unconnected-(U1-D+-Pad1)'), ('2', 'D-', 'unconnected-(U1-D--Pad2)')])]))
        findings, _ = atlas._assess_usb(facts)
        self.assertEqual(len(findings), 1)
        self.assertIn('intentionally unused', findings[0]['basis'])
        wired = atlas.design_facts(packet_for([
            part('U1', 'MCU', [('1', 'D+', 'DP'), ('2', 'D-', 'DM')]),
            part('J1', 'USB', [('1', 'DP', 'DP'), ('2', 'DM', 'DM')])]))
        findings, _ = atlas._assess_usb(wired)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all('impedance' in f['basis'] for f in findings))

    def test_spi_fires_for_named_pin_with_two_parties(self):
        facts = atlas.design_facts(packet_for([
            part('U2', 'AFE', [('1', 'SCLK', 'SPI_CLK')]),
            part('U1', 'MCU', [('1', 'P0.1', 'SPI_CLK')])]))
        findings, _ = atlas._assess_spi(facts)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['scope']['components'], ['U1', 'U2'])

    def test_high_speed_skips_decoupling_style_nets(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'MCU', [('1', 'DECUSB', 'Net-(U1-DECUSB)')]),
            part('C1', '100n', [('1', '~', 'Net-(U1-DECUSB)')])]))
        findings, notes = atlas._assess_high_speed(facts)
        self.assertEqual(findings, [])
        self.assertTrue(notes)

    def test_part_identity_wording_names_the_missing_match(self):
        facts = atlas.design_facts(packet_for([part('U1', 'nRF52840')]))
        findings, _ = atlas._assess_part_identity(facts)
        self.assertEqual(len(findings), 1)
        self.assertIn('no matched manufacturer part number', findings[0]['basis'])

    def test_crystal_marked_part_on_supply_questions_oscillator_vs_crystal(self):
        facts = atlas.design_facts(packet_for([
            part('Y2', 'Crystal_GND24', [('4', '4', '+3.3V'), ('2', '2', 'GND'),
                                         ('3', '3', 'FCLK'), ('1', '1', '')])]))
        findings, _ = atlas._assess_crystal_load(facts)
        self.assertEqual(len(findings), 1)
        self.assertIn('oscillator versus crystal', findings[0]['basis'])
        self.assertEqual(findings[0]['scope']['components'], ['Y2'])

    def test_proper_crystal_with_two_terminals_stays_quiet(self):
        facts = atlas.design_facts(packet_for([
            part('Y1', 'Crystal_GND24', [('1', '1', 'XC1'), ('3', '3', 'XC2'),
                                         ('2', '2', 'GND'), ('4', '4', 'GND')])]))
        findings, notes = atlas._assess_crystal_load(facts)
        self.assertEqual(findings, [])
        self.assertTrue(any('Y1' in n for n in notes))

    def test_nordic_crystal_pin_names_count_for_load_check(self):
        facts = atlas.design_facts(packet_for([
            part('U1', 'nRF52840', [('1', 'XC1', 'XC1'), ('2', 'XC2', 'XC2')])]))
        findings, _ = atlas._assess_crystal_load(facts)
        self.assertEqual(len(findings), 1)
        self.assertIn('no visible load capacitors', findings[0]['basis'])

    def test_truncation_records_dropped_identities(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = atlas.discover(packet, book, limit=2)
        self.assertGreater(summary['truncated'], 0)
        self.assertEqual(len(summary['dropped']), summary['truncated'])
        self.assertTrue(all('[' in d for d in summary['dropped']))
        event = next(e for e in book['events'] if e['kind'] == 'atlas_assessed')
        self.assertEqual(event['data']['dropped'], summary['dropped'])


if __name__ == '__main__':
    unittest.main()
