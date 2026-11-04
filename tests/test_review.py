"""Interpretation must not hide concrete defects or turn missing checks into a pass."""

from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.models import Component, ComponentRecord, Issue, Pad, Pin, Project
from pcb_checker.review import build_review
from pcb_checker.checks.circuit import inspect_net, inspect_pin, part_rule
from pcb_checker.report import html_report, save_report


def component(ref, value, pins=(), pads=()):
    return Component(ref,
        [ComponentRecord(ref, value, 'test:package', 'test.kicad_sch', 1)],
        [ComponentRecord(ref, value, 'test:package', 'test.kicad_pcb', 1, pads=list(pads))], list(pins))


def fixture():
    p = Project('fixture', connectivity={'status': 'compared', 'pins_compared': 5, 'schematic_nets': 3})
    p.components['U42'] = component('U42', 'MCP73831-2-MC',
        [Pin('6', 'VSS', 'power_in', 'GND'), Pin('8', 'PROG', 'input', 'PROG')],
        [Pad('6', 'GND', 6), Pad('8', 'PROG', 8), Pad('9', '', 9)])
    p.components['R99'] = component('R99', '10k', [Pin('1', net='PROG'), Pin('2', net='GND')])
    p.schematic_nets = {'GND': ['R99.2', 'U42.6'], 'PROG': ['R99.1', 'U42.8']}
    p.issues.append(Issue('warning', 'PCB_PAD_MISSING_FROM_SYMBOL', 'extra pad', component='U42', details={'pad_number': '9'}))
    return p


class ReviewTests(unittest.TestCase):
    def test_footprint_prefixed_drc_items_link_their_component(self):
        # FX4 precision defect: "Footprint R24" items left footprint groups
        # with empty components. Quoted footprint names must not invent refs.
        from pcb_checker.review import _references
        issue = Issue('warning', 'KICAD_DRC_LIB_FOOTPRINT_MISMATCH', 'mismatch', source='kicad_drc',
                      items=[{'description': 'Footprint R24'},
                             {'description': "Footprint 'R_0603_1608Metric'"}])
        self.assertEqual(_references(issue), ['R24'])
        control = Issue('warning', 'KICAD_DRC_CLEARANCE', 'x', source='kicad_drc',
                        items=[{'description': 'Track of J3'}])
        self.assertEqual(_references(control), ['J3'])

    def test_unlisted_pad_grouping_is_honest_and_never_user_homework(self):
        from pcb_checker.review import _classification
        code, category, priority, title, why, action = _classification(
            Issue('warning', 'PCB_PAD_MISSING_FROM_SYMBOL', 'SW9 pad MP has no pin in the exported schematic netlist.',
                  source='test', component='SW9'))
        self.assertEqual(title, 'Footprint pads missing from the symbol')
        self.assertIn('no edit is supported', action.lower())
        self.assertNotIn('Verify', title + why + action)
        self.assertNotIn('review mechanical', (why + action).lower())

    def test_via_group_deduplicates_items_and_preserves_original_findings(self):
        p = Project('vias')
        for uuid in ['a', 'b']:
            for code in ['ANNULAR_WIDTH', 'VIA_DIAMETER', 'DRILL_OUT_OF_RANGE']:
                p.issues.append(Issue('error', 'KICAD_DRC_' + code, 'too small', source='kicad_drc', items=[{'uuid': uuid}]))
        before = [asdict(i) for i in p.issues]
        r = build_review(p)
        self.assertEqual(r['groups'][0]['item_count'], 2)
        self.assertEqual(r['groups'][0]['raw_count'], 6)
        self.assertEqual(before, [asdict(i) for i in p.issues])
        self.assertEqual(len(r['groups'][0]['issue_ids']), 6)

    def test_short_and_connectivity_mismatch_remain_actionable(self):
        p = Project('faults', issues=[Issue('error', 'KICAD_DRC_SHORTING_ITEMS', 'short'),
                                     Issue('error', 'PCB_NET_MERGES_SCHEMATIC_NETS', 'merged'),
                                     Issue('warning', 'KICAD_DRC_SILK_OVERLAP', 'overlap')])
        r = build_review(p)
        self.assertEqual(r['fix_count'], 2)
        self.assertEqual([g['priority'] for g in r['groups']], ['fix', 'fix', 'note'])

    def test_missing_engine_and_unknown_errors_never_disappear(self):
        p = Project('partial', issues=[Issue('info', 'KICAD_UNAVAILABLE', 'missing'),
                                     Issue('error', 'NEW_UNKNOWN_RULE', 'unknown')])
        r = build_review(p)
        self.assertTrue(r['coverage_incomplete'])
        self.assertIn('not been verified', r['headline'])
        self.assertEqual({fid for g in r['groups'] for fid in g['issue_ids']}, {'F0001', 'F0002'})
        self.assertEqual(next(g for g in r['groups'] if g['issue_ids'] == ['F0002'])['priority'], 'review')

    def test_mcp_rule_is_part_based_and_includes_programmed_current(self):
        p = fixture(); p.review = build_review(p)
        g = p.review['groups'][0]
        self.assertEqual(g['components'], ['U42'])
        self.assertIn('100 mA', g['summary'])
        self.assertIn('R99', g['summary'])
        self.assertEqual(g['issue_ids'], ['F0001'])
        self.assertTrue(g['sources'][0]['url'].startswith('https://ww1.microchip.com/'))
        self.assertIn('not proof', g['why'])

    def test_grounded_ep_does_not_generate_datasheet_connection_warning(self):
        p = fixture(); p.components['U42'].pcb[0].pads[-1].net = 'GND'
        build_review(p)
        self.assertEqual(p.circuit['part_findings'], [])

    def test_part_rules_do_not_guess_from_similar_names_or_other_packages(self):
        for value in ['MCP73831-2-OT', 'MCP73832-2-MC', 'MAX30105EFD_T', 'my MAX30102EFD_T board']:
            self.assertIsNone(part_rule(component('U1', value)), value)
        conflicting = component('U1', 'MAX30102EFD_T')
        conflicting.pcb[0].value = 'MAX30105EFD_T'
        self.assertIsNone(part_rule(conflicting))

    def test_nc_pad_connected_and_missing_cases_are_not_dismissed(self):
        p = Project('nc', connectivity={'status': 'compared'})
        pads = [Pad(n, '', int(n)) for n in ('1','5','6','7','8','14')]
        p.components['U77'] = component('U77', 'MAX30102EFD_T', pads=pads)
        self.assertEqual(build_review(p)['groups'][0]['category'], 'expected')
        pads[0].net = '+3V3'
        # component constructor copies the list, but preserves the Pad objects.
        self.assertEqual(build_review(p)['groups'][0]['category'], 'electrical')
        pads[0].net = ''
        p.components['U77'].pcb[0].pads.pop()
        self.assertEqual(build_review(p)['groups'][0]['category'], 'assembly')

    def test_no_datasheet_pass_without_netlist(self):
        p = fixture(); p.connectivity = {'status': 'not_performed'}
        build_review(p)
        self.assertEqual(p.circuit['part_findings'], [])
        self.assertEqual(p.circuit['status'], 'unavailable')

    def test_nc_pad_on_shared_generated_net_is_still_a_concern(self):
        p = Project('nc', connectivity={'status': 'compared'})
        pads = [Pad(n, '', int(n)) for n in ('1','5','6','7','8','14')]
        pads[0].net = 'unconnected-(U77-NC-Pad1)'
        p.pcb_nets = {pads[0].net: ['U77.1', 'R1.1']}
        p.components['U77'] = component('U77', 'MAX30102EFD_T', pads=pads)
        self.assertEqual(build_review(p)['groups'][0]['category'], 'electrical')

    def test_bus_pullups_and_bypass_are_topology_facts(self):
        p = Project('bus', connectivity={'status': 'compared'})
        p.components['U1'] = component('U1', 'unknown', [Pin('1', 'VDD', 'power_in', '+3V3')])
        p.components['C1'] = component('C1', '100n', [Pin('1', net='+3V3'), Pin('2', net='GND')])
        p.components['R1'] = component('R1', '4k7', [Pin('1', net='SDA'), Pin('2', net='+3V3')])
        p.schematic_nets = {'SDA': ['R1.1'], 'SCL': [], '+3V3': ['U1.1','C1.1','R1.2']}
        build_review(p)
        self.assertEqual(len(p.circuit['bypass_capacitors']), 1)
        buses = {b['net']:b for b in p.circuit['bus_candidates']}
        self.assertEqual(buses['SDA']['pullups'][0]['reference'], 'R1')
        self.assertEqual(buses['SCL']['pullups'], [])
        self.assertEqual(p.issues, [])  # No invented generic electrical errors.
        p.components['R1'].schematic[0].dnp = True
        build_review(p)
        self.assertEqual(p.circuit['bus_candidates'][0]['pullups'], [])

    def test_net_and_pin_queries_report_actual_peers(self):
        p = fixture()
        self.assertIn('R99.1', inspect_pin(p, 'U42.8'))
        self.assertIn('U42.6', inspect_net(p, 'GND'))
        for query in ['missing', 'U42.missing']:
            with self.assertRaises(ValueError): inspect_pin(p, query)

    def test_html_links_resolve_and_untrusted_names_are_escaped(self):
        p = fixture(); p.archive = '<script>alert(1)</script>'
        p.review = build_review(p)
        html = html_report(p)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('id="F0001"', html)
        self.assertIn('href="#F0001"', html)
        self.assertIn('id="component-U42"', html)
        self.assertIn('AI review has not run', html)
        self.assertNotIn('{{', html)
        with tempfile.TemporaryDirectory() as td:
            out = save_report(p, td)
            packet = json.loads((out/'ai-input.json').read_text())
            self.assertEqual(packet['components'][0]['reference'], 'U42')
            self.assertTrue((out/'ai-prompt.txt').exists())


    def test_identity_issue_merges_into_same_component_part_finding(self):
        from pcb_checker.review import _merge_identity_issues_into_part_findings
        p = Project('merge')
        p.issues.append(Issue('warning', 'VALUE_MISMATCH', 'R12: schematic 10k; PCB 11k.', component='R12'))
        p.issues.append(Issue('warning', 'KICAD_DRC_CLEARANCE', 'tight', component='R12'))
        part = {'reference': 'R12', 'rule': 'led', 'components': ['R12'], 'issue_ids': [], 'raw_count': 0,
                'evidence': ['sch-only'], 'title': 'LED current'}
        value_group = {'components': ['R12'], 'issue_ids': ['F0001'], 'raw_count': 1,
                       'evidence': ['sch+pcb'], 'title': 'Value mismatch'}
        drc_group = {'components': ['R12'], 'issue_ids': ['F0002'], 'raw_count': 1,
                     'evidence': ['pcb-only'], 'title': 'Clearance'}
        groups = [part, value_group, drc_group]
        _merge_identity_issues_into_part_findings(p, groups)
        self.assertEqual(len(groups), 2)
        self.assertEqual(part['issue_ids'], ['F0001'])
        self.assertIn('sch+pcb', part['evidence'])
        self.assertEqual(part['merged_issue_codes'], ['VALUE_MISMATCH'])
        self.assertIn(drc_group, groups)

    def test_same_cause_part_groups_merge_and_list_every_component(self):
        from pcb_checker.review import _merge_same_cause_part_groups, _finding_outcome
        p = fixture()
        groups = [
            {'rule': 'led_series_resistance', 'reference': 'R12', 'pads': [],
             'title': 'R12: LED series resistance may starve the LED',
             'components': ['R12'], 'issue_ids': [], 'raw_count': 0, 'item_count': 0,
             'evidence': ['sch:R12'], 'basis': 'Sourced part rule', 'confidence': 'high',
             'priority': 'review', 'category': 'electrical', 'steps': ['check current'],
             '_merge_key': ('led_series_resistance', 'LED series resistance may starve the LED')},
            {'rule': 'led_series_resistance', 'reference': 'R13', 'pads': [],
             'title': 'R13: LED series resistance may starve the LED',
             'components': ['R13'], 'issue_ids': [], 'raw_count': 0, 'item_count': 0,
             'evidence': ['sch:R13'], 'basis': 'Sourced part rule', 'confidence': 'high',
             'priority': 'review', 'category': 'electrical', 'steps': ['check current'],
             '_merge_key': ('led_series_resistance', 'LED series resistance may starve the LED')},
        ]
        merged = _merge_same_cause_part_groups(p, groups)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['components'], ['R12', 'R13'])
        self.assertIn('R12', merged[0]['title'])
        self.assertIn('R13', merged[0]['title'])
        self.assertIn('sch:R12', merged[0]['evidence'])
        self.assertIn('sch:R13', merged[0]['evidence'])
        outcome, _why = _finding_outcome(p, merged[0])
        self.assertEqual(outcome, 'conditional')

    def test_automatic_groups_stay_separate_for_one_click_edits(self):
        from pcb_checker.review import _merge_same_cause_part_groups
        from pcb_checker.models import Issue
        p = fixture()
        p.issues.append(Issue('warning', 'VALUE_MISMATCH', 'R1: schematic 10k; PCB 11k.', component='R1'))
        p.issues.append(Issue('warning', 'VALUE_MISMATCH', 'R2: schematic 10k; PCB 12k.', component='R2'))
        first_id = 'F%04d' % (len(p.issues) - 1)
        second_id = 'F%04d' % len(p.issues)
        groups = [
            {'rule': 'value_mismatch', 'reference': 'R1', 'pads': [],
             'title': 'x', 'components': ['R1'], 'issue_ids': [first_id], 'raw_count': 1,
             'item_count': 0, 'evidence': [], 'basis': 'Sourced part rule', 'confidence': 'high',
             'priority': 'fix', 'category': 'electrical',
             '_merge_key': ('value_mismatch', 'same title')},
            {'rule': 'value_mismatch', 'reference': 'R2', 'pads': [],
             'title': 'x', 'components': ['R2'], 'issue_ids': [second_id], 'raw_count': 1,
             'item_count': 0, 'evidence': [], 'basis': 'Sourced part rule', 'confidence': 'high',
             'priority': 'fix', 'category': 'electrical',
             '_merge_key': ('value_mismatch', 'same title')},
        ]
        merged = _merge_same_cause_part_groups(p, groups)
        self.assertEqual(len(merged), 2)

    def test_every_group_gets_an_explicit_outcome(self):
        from pcb_checker.review import _finding_outcome
        p = fixture()
        info, _ = _finding_outcome(p, {'priority': 'note', 'steps': []})
        self.assertEqual(info, 'informational')
        cond, _ = _finding_outcome(p, {'priority': 'review', 'steps': ['measure X']})
        self.assertEqual(cond, 'conditional')
        inv, why = _finding_outcome(p, {'priority': 'review', 'steps': []})
        self.assertEqual(inv, 'investigation_required')
        self.assertIn('insufficient', why)

    def test_mechanical_mount_pad_is_note_not_review(self):
        from pcb_checker.review import _classification
        issue = Issue('warning', 'PCB_PAD_MISSING_FROM_SYMBOL', 'SW1 pad MP ...', component='SW1',
                      details={'pad_number': 'MP', 'mechanical_mount': True})
        code, _category, priority, title, _why, _action = _classification(issue)
        self.assertEqual(priority, 'note')
        self.assertIn('Mechanical', title)
        control = Issue('warning', 'PCB_PAD_MISSING_FROM_SYMBOL', 'U3 pad 4 ...', component='U3',
                        details={'pad_number': '4', 'mechanical_mount': False})
        self.assertEqual(_classification(control)[2], 'review')

    def test_pad_gap_groups_carry_named_pad_targets(self):
        from pcb_checker.models import Pad, Pin
        p = Project('gaps')
        comp = component('U3', 'LD1117', pads=[Pad('4', '', 1)])
        comp.schematic = [__import__('pcb_checker.models', fromlist=['ComponentRecord']).ComponentRecord(
            'U3', 'LD1117', 'SOT-223', 's.kicad_sch', 1, properties={'Datasheet': 'https://example.test/ld1117.pdf'})]
        comp.schematic_pins = [Pin('2', 'VO', 'power_out', '+3V3')]
        p.components['U3'] = comp
        p.issues.append(Issue('warning', 'PCB_PAD_MISSING_FROM_SYMBOL', 'U3 pad 4 ...',
                             component='U3', details={'pad_number': '4', 'mechanical_mount': False,
                                                       'datasheet_url': 'https://example.test/ld1117.pdf'}))
        groups = build_review(p)['groups']
        gap = [g for g in groups if g.get('components') == ['U3']]
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0]['chain_targets'], [{'reference': 'U3', 'pin': '4'}])
        steps = ' '.join(gap[0].get('steps', []))
        self.assertIn('U3 pad 4', steps)
        self.assertIn('https://example.test/ld1117.pdf', steps)

    def test_identity_issue_without_part_finding_stays_standalone(self):
        from pcb_checker.review import _merge_identity_issues_into_part_findings
        p = Project('nomerge')
        p.issues.append(Issue('warning', 'VALUE_MISMATCH', 'R7 mismatch.', component='R7'))
        lone = {'components': ['R7'], 'issue_ids': ['F0001'], 'raw_count': 1,
                'evidence': ['e'], 'title': 'Value mismatch'}
        groups = [lone]
        _merge_identity_issues_into_part_findings(p, groups)
        self.assertEqual(groups, [lone])


if __name__ == '__main__':
    unittest.main()
