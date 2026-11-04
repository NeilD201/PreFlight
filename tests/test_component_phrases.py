"""Verified component phrases: values, endpoints and strict function words."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.component_phrases import (
    describe_component, describe_all, prettify_value, display_name)


def project_with(pins_by_ref, nets, values=None, symbols=None):
    components = {}
    for ref, pins in pins_by_ref.items():
        value = (values or {}).get(ref, '')
        symbol = (symbols or {}).get(ref, '')
        components[ref] = {
            'reference': ref,
            'schematic': [{'reference': ref, 'value': value, 'symbol': symbol,
                           'properties': {'Description': ''}}],
            'pcb': [],
            'schematic_pins': [{'number': str(i), 'name': name, 'net': net}
                               for i, (name, net) in enumerate(pins)]}
    return {'components': components, 'schematic_nets': nets}


class PhraseTests(unittest.TestCase):
    def test_value_prettify(self):
        self.assertEqual(prettify_value('100nf', 'capacitor'), '100 nF')
        self.assertEqual(prettify_value('10k', 'resistor'), '10 k\u03a9')
        self.assertEqual(prettify_value('4R7', 'resistor'), '4.7 \u03a9')
        self.assertEqual(prettify_value('1N4148', 'diode'), '1N4148')
        self.assertEqual(prettify_value('D', 'diode'), '')
        self.assertEqual(prettify_value('RotaryEncoder_Switch', 'rotary encoder'), '')
        self.assertEqual(prettify_value('MCP23017_ML', 'chip'), 'MCP23017_ML')
        self.assertEqual(prettify_value('~', 'capacitor'), '')

    def test_pull_down_needs_ic_and_ground(self):
        project = project_with(
            {'R12': [('1', 'X'), ('2', 'GND')], 'U1': [('RESET', 'X'), ('VSS', 'GND')]},
            {'X': ['R12.0', 'U1.0'], 'GND': ['R12.1', 'U1.1']},
            {'R12': '10k', 'U1': 'MCU'}, {'U1': 'MCU:MCU'})
        described = describe_component(project, 'R12')
        self.assertEqual(described['function'], 'pull-down')
        self.assertIn('(R12)', described['phrase'])
        self.assertIn('ground', described['phrase'])
        self.assertIn('U1', described['phrase'])

    def test_pull_up_needs_supply_and_ic(self):
        project = project_with(
            {'R3': [('1', 'X'), ('2', '+3V3')], 'U1': [('SDA', 'X'), ('VDD', '+3V3')]},
            {'X': ['R3.0', 'U1.0'], '+3V3': ['R3.1', 'U1.1']},
            {'R3': '4k7', 'U1': 'MCU'}, {'U1': 'MCU:MCU'})
        described = describe_component(project, 'R3')
        self.assertEqual(described['function'], 'pull-up')
        self.assertIn('+3V3', described['phrase'])

    def test_resistor_without_ic_stays_neutral(self):
        project = project_with(
            {'R9': [('1', 'A'), ('2', 'B')]},
            {'A': ['R9.0', 'J1.0'], 'B': ['R9.1', 'J1.1']},
            {'R9': '10k'})
        described = describe_component(project, 'R9')
        self.assertEqual(described['function'], '')
        self.assertIn('resistor (R9)', described['short'])

    def test_decoupling_needs_supply_and_ground(self):
        project = project_with(
            {'C8': [('1', 'VDD'), ('2', 'GND')], 'U2': [('VDD', 'VDD'), ('VSS', 'GND')]},
            {'VDD': ['C8.0', 'U2.0'], 'GND': ['C8.1', 'U2.1']},
            {'C8': '100n', 'U2': 'REG'}, {'U2': 'Reg:Reg'})
        described = describe_component(project, 'C8')
        self.assertEqual(described['function'], 'decoupling capacitor')
        self.assertIn('ground', described['phrase'])

    def test_unknown_reference_returns_none(self):
        project = project_with({}, {})
        self.assertIsNone(describe_component(project, 'R99'))

    def test_display_names_use_values_not_designators(self):
        project = project_with(
            {'R16': [], 'J1': [], 'U2': [], 'RST1': [], 'C105': []},
            {},
            {'R16': '4.7k', 'J1': 'USB_C_Receptacle_USB2.0_16P',
             'U2': 'RP2040', 'RST1': 'SW_Push', 'C105': '100uf'},
            {'R16': 'Device:R', 'J1': 'Connector:USB_C_Receptacle_USB2.0_16P',
             'U2': 'MCU_RaspberryPi:RP2040', 'RST1': 'Switch:SW_Push',
             'C105': 'Device:C_Small'})
        project['components']['J1']['schematic'][0]['properties']['Description'] = (
            'USB 2.0-only 16P Type-C Receptacle connector')
        project['components']['U2']['schematic'][0]['properties']['Description'] = (
            'A microcontroller by Raspberry Pi')
        project['components']['RST1']['schematic'][0]['properties']['Description'] = (
            'Push button switch, generic, two pins')
        names = {ref: display_name(ref, project['components'][ref]) for ref in project['components']}
        self.assertEqual(names['R16'], '4.7 kΩ resistor (R16)')
        self.assertEqual(names['J1'], 'USB-C connector (J1)')
        self.assertEqual(names['U2'], 'RP2040 microcontroller (U2)')
        self.assertEqual(names['RST1'], 'push button (RST1)')
        self.assertEqual(names['C105'], '100 µF capacitor (C105)')
        # Never a bare designator, never a stutter, never a symbol dump.
        for ref, name in names.items():
            self.assertNotIn('the the', name)
            self.assertTrue(name.endswith(f'({ref})'), name)
            self.assertNotIn('_', name.split('(')[0], name)

    def test_truncated_connections_are_disclosed(self):
        project = project_with(
            {'J1': [('1', 'A'), ('2', 'B'), ('3', 'C'), ('4', 'G')]},
            {'A': ['J1.0'], 'B': ['J1.1'], 'C': ['J1.2'], 'G': ['J1.3']},
            {'J1': ''}, {'J1': 'Conn:Conn'})
        described = describe_component(project, 'J1')
        self.assertIn('(plus 2 more connections)', described['phrase'])
        self.assertIn('(J1)', described['phrase'])

    def test_macropad_encoder_phrase(self):
        fixture = Path('reports/selected-project-20261030-220916-q1mva17w/report.json')
        if not fixture.is_file():
            self.skipTest('Fixture report is ephemeral local run output, not a repo fixture.')
        report = json.loads(fixture.read_text())
        described = describe_component(report, 'SW9')
        self.assertIn('(SW9)', described['phrase'])
        self.assertIn('ground', described['phrase'])
        self.assertIn('U1', described['phrase'])
        self.assertIn('MCP23017', described['phrase'])
        # No invented role: the expander must not be called a microcontroller.
        self.assertNotIn('microcontroller', described['phrase'].lower())
        self.assertNotIn('~{', described['phrase'])
        self.assertNotIn('_ML', described['phrase'])

    def test_macropad_and_wenneboard_run_clean(self):
        names = ['selected-project-20261030-220916-q1mva17w',
                 'wenne_fx4-20261030-234337-m4cr_2qc',
                 'macro_fresh-20261030-223936-5_0mi1ks']
        # Fixture reports are ephemeral local run output, not repo fixtures.
        names = [n for n in names if Path(f'reports/{n}/report.json').is_file()]
        if not names:
            self.skipTest('No saved fixture reports present; run and keep a review to exercise this.')
        for name in names:
            report = json.loads(Path(f'reports/{name}/report.json').read_text())
            all_described = describe_all(report)
            self.assertGreater(len(all_described), 10, name)
            for ref, described in all_described.items():
                self.assertIn(f'({ref})', described['phrase'], f'{name} {ref}')
                self.assertNotIn('None', described['phrase'], f'{name} {ref}')


if __name__ == '__main__':
    unittest.main()
