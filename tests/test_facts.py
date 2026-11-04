"""Shared fact predicates: rails, grounds, unconnected pads, heaters, regulators."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import facts


class SupplyTests(unittest.TestCase):
    def test_dotted_and_signed_kicad_rails(self):
        for net in ('+3.3V', '+5V', '+1V8', '3V3', '3.3V', '+Bat', 'VBAT',
                    'VBUS', 'VCC', 'VCC_3V3', 'VIN', 'VDDIO', '1.8V', '+12V'):
            self.assertTrue(facts.is_supply_net(net), net)

    def test_signals_and_grounds_are_not_supplies(self):
        for net in ('GND', 'AGND', 'SDA', 'SCL', 'Net-(U1-Pad1)', '', 'VLED+', 'TX'):
            self.assertFalse(facts.is_supply_net(net), net)

    def test_grounds(self):
        for net in ('GND', 'AGND', 'DGND', 'GNDA', 'PGND', 'VSS', 'GND_3V3', '-Bat', 'BAT-'):
            self.assertTrue(facts.is_ground_net(net), net)
        for net in ('+Bat', '+3.3V', '-5V', 'SDA', '', 'SIGNAL'):
            self.assertFalse(facts.is_ground_net(net), net)

    def test_battery_negative_is_not_a_supply(self):
        self.assertFalse(facts.is_supply_net('-Bat'))
        self.assertTrue(facts.is_supply_net('-5V'))

    def test_synthetic_unconnected_nets(self):
        self.assertTrue(facts.is_unconnected_net('unconnected-(U13-D+-PadAD6)'))
        self.assertFalse(facts.is_unconnected_net('Net-(U1-Pad1)'))
        self.assertFalse(facts.is_unconnected_net(''))
        self.assertFalse(facts.is_unconnected_net(None))


class DissipatorTests(unittest.TestCase):
    def comp(self, value='', footprint='', mpn='', dnp=False):
        return {'value': value, 'footprint': footprint, 'mpn': mpn, 'dnp': dnp}

    def test_regulator_and_charger_stems_match_without_word_boundary(self):
        components = {'U4': self.comp('AP2112K-3.3'), 'U8': self.comp('MCP73831-2-MC'),
                      'U1': self.comp('AMS1117'), 'M1': self.comp('Motor_AC'),
                      'Q1': self.comp('FS8205A')}
        hits = facts.power_dissipators(components)
        self.assertEqual(hits, ['U1', 'U4', 'U8'])

    def test_small_indicator_leds_stay_out_power_leds_stay_in(self):
        components = {'D1': self.comp('LED', 'LED_SMD:LED_0201_0603Metric'),
                      'D2': self.comp('LED', 'LED_SMD:LED_2835_3228Metric'),
                      'D3': self.comp('LED')}
        hits = facts.power_dissipators(components)
        self.assertNotIn('D1', hits)
        self.assertIn('D2', hits)
        self.assertIn('D3', hits)

    def test_passives_and_dnp_stay_out(self):
        components = {'R1': self.comp('10k'), 'U9': self.comp('AP2112K-3.3', dnp=True)}
        self.assertEqual(facts.power_dissipators(components), [])

    def test_regulator_roles(self):
        self.assertTrue(facts.looks_like_regulator('AP2112K-3.3'))
        self.assertTrue(facts.looks_like_regulator('MCP73831-2-MC'))
        self.assertTrue(facts.looks_like_regulator('LM317'))
        self.assertFalse(facts.looks_like_regulator('X'))
        self.assertFalse(facts.looks_like_regulator('10k'))


if __name__ == '__main__':
    unittest.main()
