"""Scenario-driven discovery: startup, operation, misuse, environment, assembly."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import atlas, design_scan, ledger, reviewer, scenario_scan
from test_circuit_audit import fixture


def packet_for(components, operating=None):
    packet = {'components': components}
    if operating is not None:
        packet['design_context'] = {'operating_context': operating}
    return packet


def part(ref, value='', pins=(), dnp=False):
    return {'reference': ref, 'value': value, 'footprint': '', 'mpn': '', 'dnp': dnp,
            'pins': [{'number': n, 'name': name, 'net': net} for n, name, net in pins]}


class ScenarioTests(unittest.TestCase):
    def test_two_domains_question_sequencing_single_domain_inrush(self):
        data = scenario_scan.facts(packet_for([
            part('U1', pins=[('1', 'VDD', 'VCC')]), part('U2', pins=[('1', 'VDD', 'VBUS')])]))
        findings, _ = scenario_scan.startup_power(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('sequencing', findings[0]['basis'])
        data = scenario_scan.facts(packet_for([
            part('U1', pins=[('1', 'VDD', 'VCC')]), part('U2', pins=[('1', 'VDD', 'VCC')])]))
        findings, _ = scenario_scan.startup_power(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('inrush', findings[0]['basis'])
        data = scenario_scan.facts(packet_for([part('U1', pins=[('1', 'VDD', 'VCC')])]))
        self.assertEqual(scenario_scan.startup_power(data)[0], [])

    def test_reset_timing_needs_slowest_ramp(self):
        data = scenario_scan.facts(packet_for([part('U1', pins=[('7', 'NRST', 'RST')])]))
        findings, _ = scenario_scan.reset_timing(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['scope']['pins'], ['U1.7'])
        data = scenario_scan.facts(packet_for([part('U1', pins=[('1', 'VDD', 'VCC')])]))
        self.assertEqual(scenario_scan.reset_timing(data)[0], [])

    def test_heating_and_environment_pair_stated_ambient(self):
        data = scenario_scan.facts(packet_for(
            [part('U1', 'AMS1117', [('1', 'VIN', 'V5')])], operating={'temperature': '0 to 50 C'}))
        self.assertEqual(len(scenario_scan.sustained_heating(data)[0]), 1)
        findings, notes = scenario_scan.environment(data)
        self.assertEqual(findings, [])
        self.assertTrue(any('0 to 50 C' in n for n in notes))
        data = scenario_scan.facts(packet_for([part('U1', 'AMS1117', [('1', 'VIN', 'V5')])]))
        findings, _ = scenario_scan.environment(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('ambient temperature', findings[0]['missing'][0])

    def test_connector_misuse_and_dnp_intent(self):
        data = scenario_scan.facts(packet_for([
            part('J1', pins=[('1', 'S', 'SIG')]),
            part('C9', '100n', [('1', '~', 'SIG')]), part('R9', '10k', [('1', '~', 'OTHER')])],
            ))
        data['components']['C9']['dnp'] = True
        findings, _ = scenario_scan.connector_misuse(data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]['scope']['components'], ['J1'])
        findings, _ = scenario_scan.dnp_intent(data)
        scoped = {f['scope']['components'][0] for f in findings}
        self.assertEqual(scoped, {'C9'})
        data = scenario_scan.facts(packet_for([part('U1', pins=[('1', 'VDD', 'VCC')])]))
        self.assertEqual(scenario_scan.connector_misuse(data)[0], [])
        self.assertEqual(scenario_scan.dnp_intent(data)[0], [])


class DiscoveryTests(unittest.TestCase):
    def test_fixture_discovery_adds_startup_claim(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = scenario_scan.discover(packet, book)
        self.assertEqual(summary['checks'], 6)
        self.assertEqual(summary['triggered'], ['SCN-001'])
        startup = [c for c in book['claims'] if '[SCN-001]' in c['statement']]
        self.assertEqual(len(startup), 1)
        self.assertEqual(startup[0]['origin'], 'scenario')
        self.assertIn('VCC', startup[0]['scope']['nets'])
        event = next(e for e in book['events'] if e['kind'] == 'scenario_assessed')
        self.assertIn('SCN-004', event['data']['excluded'])
        self.assertNotIn('secret', json.dumps(book))

    def test_streams_stay_independent_until_merge(self):
        packet = reviewer.evidence_packet(fixture())
        solo = ledger.new_ledger({})
        scenario_scan.discover(packet, solo)
        solo_statements = sorted(c['statement'] for c in solo['claims'])
        merged = ledger.new_ledger({})
        atlas.discover(packet, merged, origin='scope')
        design_scan.discover(packet, merged, origin='circuit')
        scenario_scan.discover(packet, merged, origin='scenario')
        scenario_statements = sorted(c['statement'] for c in merged['claims'] if c['origin'] == 'scenario')
        self.assertEqual(solo_statements, scenario_statements)
        self.assertTrue(solo_statements)
        self.assertEqual(len(merged['claims']),
                         len({c['statement'] for c in merged['claims']}))

    def test_recording_budget_reports_truncation(self):
        packet = reviewer.evidence_packet(fixture())
        book = ledger.new_ledger({})
        summary = scenario_scan.discover(packet, book, limit=0)
        self.assertEqual(summary['claims'], [])
        self.assertGreater(summary['truncated'], 0)
        self.assertTrue(any('budget' in limitation for limitation in book['limitations']))


class RealBoardRegressionTests(unittest.TestCase):
    def test_dotted_rails_form_sequencing_domains(self):
        data = scenario_scan.facts(packet_for([
            part('U1', pins=[('1', 'VDD', '+3.3V')]), part('U2', pins=[('1', 'VDD', '+1V8')])]))
        findings, _ = scenario_scan.startup_power(data)
        self.assertEqual(len(findings), 1)
        self.assertIn('+3.3V', findings[0]['basis'])

    def test_regulator_stems_heat_but_small_leds_do_not(self):
        hot = scenario_scan.facts(packet_for([part('U4', 'AP2112K-3.3', [('1', 'VIN', 'V5')])]))
        self.assertEqual(len(scenario_scan.sustained_heating(hot)[0]), 1)
        cold = {'components': {'D1': {'value': 'LED', 'footprint': 'LED_SMD:LED_0201_0603Metric',
                                      'mpn': '', 'dnp': False, 'pins': []}}, 'nets': {}, 'operating': {}}
        self.assertEqual(scenario_scan.sustained_heating(cold)[0], [])


if __name__ == '__main__':
    unittest.main()
