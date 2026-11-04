from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.checks.connectivity import attach_netlist, classify_pad_gap, compare_connectivity, pad_measurements, pcb_net_inventory
from pcb_checker.models import Component, ComponentRecord, Issue, Pad, Pin, Project, ProjectFile
from pcb_checker.native import NativeError, rule_issues, run_native
from pcb_checker.parsers.kicad_pcb import parse_pcb
from pcb_checker.parsers.netlist import parse_netlist
from pcb_checker.parsers.sexpr import ParseError
from pcb_checker.report import html_report, inspect_component, save_report


def connected_project():
    project = Project('demo')
    for ref, number, net in [('R1', '1', 'GND'), ('U1', '2', 'GND'), ('C1', '1', 'VCC')]:
        sch = ComponentRecord(ref, 'value', 'Footprint', 'demo.kicad_sch', 10)
        pcb = ComponentRecord(ref, 'value', 'Footprint', 'demo.kicad_pcb', 20,
                              pads=[Pad(number, net, 21)])
        project.components[ref] = Component(ref, [sch], [pcb], [Pin(number, 'name', 'passive', net)])
    project.schematic_nets = {'GND': ['R1.1', 'U1.2'], 'VCC': ['C1.1']}
    return project


def compare(project):
    pcb_net_inventory(project)
    compare_connectivity(project, set())
    return {issue.code for issue in project.issues}


class ConnectivityTests(unittest.TestCase):
    def test_pad_gap_mounting_leg_is_structural_not_unverified(self):
        cls, basis = classify_pad_gap('MP', [], [])
        self.assertEqual(cls, 'mounting')
        self.assertIn('mechanical', basis)

    def test_pad_gap_mounting_hole_kind_is_structural(self):
        cls, _ = classify_pad_gap('7', [], ['np_thru_hole'])
        self.assertEqual(cls, 'mounting')

    def test_pad_gap_grounded_pad_claims_no_function(self):
        cls, basis = classify_pad_gap('4', ['GND'], ['smd'])
        self.assertEqual(cls, 'grounded-pad')
        self.assertIn('does not prove', basis)

    def test_pad_gap_connected_pad_is_not_verified(self):
        cls, basis = classify_pad_gap('4', ['+3V3'], ['smd'])
        self.assertEqual(cls, 'unverified')
        self.assertIn('NOT VERIFIED', basis)

    def test_pad_gap_unconnected_unknown_pad_is_not_verified(self):
        cls, basis = classify_pad_gap('9', [], ['smd'])
        self.assertEqual(cls, 'unverified')
        self.assertIn('NOT VERIFIED', basis)

    def test_pad_gap_classification_is_recorded_on_the_issue(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads.append(Pad('9', '', 23))
        self.assertIn('PCB_PAD_MISSING_FROM_SYMBOL', compare(project))
        details = [i.details for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL'][0]
        self.assertEqual(details['pad_classification']['class'], 'unverified')
        self.assertIn('NOT VERIFIED', details['pad_classification']['basis'])

    def test_matching_nets_and_multiple_pads_of_same_number(self):
        project = connected_project()
        project.components['R1'].pcb[0].pads.append(Pad('1', 'GND', 23))
        self.assertEqual(compare(project), set())
        self.assertEqual(project.connectivity['pins_compared'], 3)
        self.assertEqual(project.pcb_nets['GND'], ['R1.1', 'U1.2'])

    def test_real_split_is_detected_even_when_all_component_fields_match(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads[0].net = 'wrong'
        codes = compare(project)
        self.assertIn('SCHEMATIC_NET_SPLIT_ON_PCB', codes)
        self.assertIn('PIN_NET_NAME_MISMATCH', codes)
        self.assertTrue(all(i.evidence for i in project.issues))

    def test_merged_nets_are_detected(self):
        project = connected_project()
        project.components['C1'].pcb[0].pads[0].net = 'GND'
        self.assertIn('PCB_NET_MERGES_SCHEMATIC_NETS', compare(project))

    def test_no_connect_pin_connected_to_board_only_component(self):
        project = connected_project()
        project.components['C1'].schematic_pins[0].no_connect = True
        board_only = ComponentRecord('H1', '', '', 'demo.kicad_pcb', 30, board_only=True,
                                     pads=[Pad('1', 'VCC', 31)])
        project.components['H1'] = Component('H1', pcb=[board_only])
        self.assertIn('NO_CONNECT_PIN_CONNECTED', compare(project))

    def test_disconnected_pin_does_not_require_a_generated_net_name(self):
        project = connected_project()
        project.schematic_nets.pop('VCC')
        pin = project.components['C1'].schematic_pins[0]
        pin.net = 'unconnected-(C1-Pad1)'
        pin.no_connect = True
        project.schematic_nets[pin.net] = ['C1.1']
        project.components['C1'].pcb[0].pads[0].net = ''
        self.assertEqual(compare(project), set())

    def test_auto_renamed_net_with_same_members_is_not_a_mismatch(self):
        # Same copper on both sides, different KiCad auto-names
        # (e.g. Net-(SW1-A) vs Net-(U4-P0)): membership agrees, so no warning.
        project = connected_project()
        project.components['R1'].schematic_pins[0].net = 'Net-(R1-1)'
        project.components['U1'].schematic_pins[0].net = 'Net-(R1-1)'
        project.schematic_nets = {'Net-(R1-1)': ['R1.1', 'U1.2'], 'VCC': ['C1.1']}
        project.components['R1'].pcb[0].pads[0].net = 'Net-(U1-2)'
        project.components['U1'].pcb[0].pads[0].net = 'Net-(U1-2)'
        project.pcb_nets = {}
        self.assertEqual(compare(project), set())

    def test_missing_and_extra_pad_numbers(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads[0].number = '3'
        self.assertTrue({'PIN_MISSING_FROM_PCB', 'PCB_PAD_MISSING_FROM_SYMBOL'} <= compare(project))

    def test_mounting_designated_unconnected_pads_are_not_user_homework(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads.extend([Pad('MP', '', 26), Pad('MP', '', 27)])
        codes = compare(project)
        self.assertIn('PCB_PAD_MISSING_FROM_SYMBOL', codes)
        messages = [i.message for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL']
        self.assertEqual(len(messages), 1)
        self.assertIn('Mounting designation (2 pad(s))', messages[0])
        self.assertIn('no edit is supported', messages[0])
        self.assertNotIn('review mechanical', messages[0])
        self.assertNotIn('Verify', messages[0])

    def test_connected_pad_without_symbol_pin_states_net_and_defers_to_datasheet(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads.append(Pad('17', 'GND', 26, 'smd'))
        self.assertIn('PCB_PAD_MISSING_FROM_SYMBOL', compare(project))
        messages = [i.message for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL']
        self.assertEqual(len(messages), 1)
        self.assertIn('connects to GND', messages[0])
        self.assertIn('datasheet', messages[0])
        self.assertIn('no edit is supported', messages[0])
        self.assertNotIn('carries no net', messages[0])
        details = [i.details for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL'][0]
        self.assertEqual(details['pad_nets'], ['GND'])
        self.assertFalse(details['mechanical_mount'])

    def test_unplated_hole_without_symbol_pin_is_mechanical(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads.append(Pad('X7', '', 26, 'np_thru_hole'))
        self.assertIn('PCB_PAD_MISSING_FROM_SYMBOL', compare(project))
        messages = [i.message for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL']
        self.assertEqual(len(messages), 1)
        self.assertIn('mounting hole', messages[0])

    def test_unknown_pad_missing_symbol_is_honest_unknown(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads.append(Pad('X7', '', 26))
        self.assertIn('PCB_PAD_MISSING_FROM_SYMBOL', compare(project))
        messages = [i.message for i in project.issues if i.code == 'PCB_PAD_MISSING_FROM_SYMBOL']
        self.assertEqual(len(messages), 1)
        self.assertIn('needs the part datasheet', messages[0])
        self.assertIn('no edit is supported', messages[0])
        self.assertNotIn('Verify', messages[0])

    def test_empty_net_and_repeated_pad_conflict(self):
        project = connected_project()
        project.components['U1'].pcb[0].pads[0].net = ''
        project.components['R1'].pcb[0].pads.append(Pad('1', 'VCC', 25))
        self.assertTrue({'PCB_PAD_WITHOUT_NET', 'PAD_NET_CONFLICT'} <= compare(project))

    def test_pad_geometry_is_parsed_from_footprint(self):
        text = ('(kicad_pcb (version 20240108) (generator test) (paper "A4")\n'
                ' (net 0 "") (net 1 "GND")\n'
                ' (footprint "X" (layer "F.Cu") (tedit 0) (at 10 20)\n'
                '  (pad "1" smd rect (at -0.65 0) (size 0.7 1.7) (layers "F.Cu"))\n'
                '  (pad "2" smd rect (at 0.65 0) (size 0.7 1.7) (layers "F.Cu"))))\n')
        comps = parse_pcb(text, 't.kicad_pcb')
        pads = {p.number: p for p in comps.components[0].pads}
        self.assertEqual((pads['1'].x_mm, pads['1'].y_mm), (-0.65, 0.0))
        self.assertEqual((pads['2'].width_mm, pads['2'].height_mm), (0.7, 1.7))

    def test_pad_measurements_report_pitch_and_never_pass(self):
        pcb = ComponentRecord('U1', 'v', 'F', 'b.kicad_pcb', 1, pads=[
            Pad('1', 'GND', 2, 'smd', '', -0.65, 0.0, 0.7, 1.7),
            Pad('2', 'VCC', 3, 'smd', '', 0.65, 0.0, 0.7, 1.7),
            Pad('3', '', 4, 'smd', '')])
        comp = Component('U1', [], [pcb], [])
        measured = pad_measurements(comp)
        self.assertEqual(measured['pad_count'], 3)
        self.assertEqual(measured['measured_pads'], 2)
        self.assertEqual(measured['unmeasured_pads'], 1)
        self.assertAlmostEqual(measured['min_pitch_mm'], 1.3)
        self.assertEqual(measured['conformance'], 'NOT VERIFIED')

    def test_unnumbered_mechanical_pads_are_not_missing_symbol_pins(self):
        project = connected_project()
        project.components['R1'].pcb[0].pads.append(Pad('', '', 25, 'np_thru_hole'))
        self.assertEqual(compare(project), set())

    def test_board_net_identifiers_and_geometry(self):
        text = '''(kicad_pcb (net 1 "GND")
          (footprint "X" (property "Reference" "R1") (at 10 20 90) (layer "B.Cu")
            (pad "1" smd rect (net 1 "GND")) (pad "" np_thru_hole circle)))'''
        record = parse_pcb(text, 'demo.kicad_pcb').components[0]
        self.assertEqual((record.x_mm, record.y_mm, record.rotation, record.layer), (10, 20, 90, 'B.Cu'))
        self.assertEqual(record.pads[0].net, 'GND')
        with self.assertRaises(ParseError):
            parse_pcb(text.replace('(net 1 "GND"))', '(net 2 "VCC"))'), 'demo.kicad_pcb')

    def test_xml_pin_functions_and_explicit_no_connect(self):
        xml = '''<export><components><comp ref="U1"/></components><nets>
          <net name="unconnected-(U1-Pad1)"><node ref="U1" pin="1" pinfunction="RESET" pintype="input+no_connect"/></net>
          </nets></export>'''
        pins, nets = parse_netlist(xml)
        self.assertEqual(pins['U1'][0].name, 'RESET')
        self.assertTrue(pins['U1'][0].no_connect)
        self.assertEqual(nets['unconnected-(U1-Pad1)'], ['U1.1'])
        with self.assertRaises(ParseError):
            parse_netlist('<!DOCTYPE export>' + xml)
        with self.assertRaises(ParseError):
            parse_netlist(xml.replace('ref="U1" pin=', 'ref="U99" pin='))

    def test_netlist_source_mismatch_rejects_partial_attachment(self):
        project = connected_project()
        xml = '<export><components><comp ref="R99"/></components><nets/></export>'
        before = asdict(project)
        with self.assertRaises(ParseError):
            attach_netlist(project, xml)
        self.assertEqual(asdict(project), before)


class NativeReportTests(unittest.TestCase):
    def test_staging_copies_needed_files_and_blocks_external_sheets(self):
        from pcb_checker.native import _stage
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, destination = root / 'source', root / 'staged'
            source.mkdir()
            destination.mkdir()
            (source / 'main.kicad_sch').write_text('(kicad_sch (sheet (property "Sheetfile" "child.kicad_sch")))')
            (source / 'child.kicad_sch').write_text('(kicad_sch)')
            (source / '_autosave-bad.kicad_sch').write_text('malformed backup')
            project = Project('demo', files=[ProjectFile(p.name, 'schematic', p.stat().st_size) for p in source.iterdir()],
                              selected_files={'schematic': 'main.kicad_sch'})
            _stage(source, project, destination)
            self.assertTrue((destination / 'child.kicad_sch').is_file())
            self.assertFalse((destination / '_autosave-bad.kicad_sch').exists())
            (source / 'main.kicad_sch').write_text('(kicad_sch (sheet (property "Sheetfile" "../outside.kicad_sch")))')
            with self.assertRaises(NativeError):
                _stage(source, project, destination)

    def test_erc_drc_and_exclusions_keep_provenance(self):
        violation = {'type': 'pin_not_connected', 'severity': 'error', 'description': 'Pin not connected',
                     'items': [{'description': 'Symbol U1 Pin 2', 'pos': {'x': 1, 'y': 2}, 'uuid': 'abc'}]}
        report = {'coordinate_units': 'mm', 'sheets': [{'path': '/', 'violations': [violation]}]}
        issue = rule_issues(report, 'erc', 'main.kicad_sch')[0]
        self.assertEqual((issue.code, issue.severity, issue.source), ('KICAD_ERC_PIN_NOT_CONNECTED', 'error', 'kicad_erc'))
        self.assertIn('UUID abc', issue.evidence[1])
        drc = {'violations': [], 'unconnected_items': [dict(violation, excluded=True)]}
        issue = rule_issues(drc, 'drc', 'main.kicad_pcb')[0]
        self.assertTrue(issue.excluded)
        self.assertEqual(issue.severity, 'info')
        with self.assertRaises(NativeError):
            rule_issues({}, 'drc', 'main.kicad_pcb')

    def test_missing_cli_is_not_a_passing_check(self):
        with patch('pcb_checker.native.find_kicad', return_value=None):
            result = run_native('missing', Project('test'))
        self.assertIsNone(result.netlist)
        self.assertEqual(result.reports, {})
        self.assertEqual(result.issues[0].code, 'KICAD_UNAVAILABLE')

    def test_subprocess_timeout_is_reported(self):
        import subprocess
        from pcb_checker.native import _run
        with tempfile.TemporaryDirectory() as temp, patch('pcb_checker.native.subprocess.run', side_effect=subprocess.TimeoutExpired('kicad', 1)):
            with self.assertRaises(NativeError):
                _run('kicad-cli', ['version'], Path(temp), timeout=1)


class ReportTests(unittest.TestCase):
    def test_saved_reports_escape_project_data_and_keep_previous_runs(self):
        project = connected_project()
        project.archive = '<script>alert(1)</script>{{script}}'
        project.issues.append(Issue('warning', 'TEST', '<img src=x onerror=alert(1)>', ['<unsafe>']))
        html = html_report(project)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img', html)
        self.assertIn('{{script}}', html)
        self.assertIn('R1', html)
        with tempfile.TemporaryDirectory() as temp:
            first = save_report(project, temp)
            second = save_report(project, temp)
            self.assertNotEqual(first, second)
            self.assertTrue((first / 'report.html').is_file())
            self.assertTrue((first / 'report.json').is_file())

    def test_inspect_component_reports_pin_and_both_nets(self):
        project = connected_project()
        text = inspect_component(project, 'U1')
        self.assertIn('Pin 2', text)
        self.assertIn('schematic GND | PCB GND', text)
        with self.assertRaises(ValueError):
            inspect_component(project, 'missing')


if __name__ == '__main__':
    unittest.main()
