import os
import unittest
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import file_fixes as fixes
from pcb_checker import fix_plan
from pcb_checker import fix_workspace as ws
from pcb_checker.analysis import analyze_project
from pcb_checker.models import Component, Issue, Pin, Project, ProjectFile
from test_components import schematic, symbol, board, footprint
from test_fix_candidate import PCB_GAP, PAD_UUID, TRACK_UUID, KICAD_CLI


class FixPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'project'
        self.source.mkdir()
        (self.source / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        board_text = board(footprint(value='100nF'))
        (self.source / 'test.kicad_pcb').write_text(board_text)
        self.project = analyze_project(self.source)
        self.value_group = next(g for g in self.project.review['groups'] if any(
            self.project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        self.before = {p: (self.source / p).read_bytes()
                       for p in ('test.kicad_sch', 'test.kicad_pcb')}

    def test_two_step_value_plan_succeeds(self):
        record = fix_plan.run_plan(self.source, self.project,
                                   [{'group': dict(self.value_group)},
                                    {'group': dict(self.value_group)}],
                                   self.root / 'workspace')
        self.assertEqual([s['status'] for s in record['steps']],
                         ['completed', 'pruned-already-resolved'])
        self.assertEqual(record['final']['target_removed'].get('VALUE_MISMATCH'), 1)
        self.assertEqual(record['final']['added_codes'], [])
        self.assertTrue(record['source_untouched'])
        for name, content in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), content)

    def test_two_mutating_steps_share_one_candidate(self):
        home = Path(self.temp.name) / 'kicadhome3'
        home.mkdir()
        two = self.root / 'two-step-project'
        two.mkdir()
        c1 = (' (footprint "Cap:C_0402" (at 5 5) (layer "F.Cu")'
                ' (uuid "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")'
                ' (property "Reference" "C1") (property "Value" "100nF"))\n')
        self.assertTrue(PCB_GAP.endswith(')'))
        pcb = PCB_GAP[:-1] + c1 + ')'
        (two / 'test.kicad_sch').write_text(schematic(symbol('C1', '10nF')))
        (two / 'test.kicad_pcb').write_bytes(pcb.encode())
        project = analyze_project(two)
        value_group = next(g for g in project.review['groups'] if any(
            project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        project.components['U1'] = Component('U1', schematic_pins=[Pin('1', net='GND')])
        project.issues.append(Issue('error', 'KICAD_DRC_UNCONNECTED_ITEMS', 'gap',
                                    items=[{'uuid': PAD_UUID}, {'uuid': TRACK_UUID}]))
        gap_group = {'id': 'R-gap', 'issue_ids': ['F%04d' % len(project.issues)]}
        before = {p: (two / p).read_bytes() for p in ('test.kicad_sch', 'test.kicad_pcb')}
        with patch.dict(os.environ, {'HOME': str(home)}):
            record = fix_plan.run_plan(two, project,
                                       [{'group': value_group}, {'group': gap_group}],
                                       self.root / 'workspace', kicad_cli=KICAD_CLI)
        self.assertEqual([s['status'] for s in record['steps']], ['completed', 'completed'])
        self.assertEqual(record['final']['target_removed'].get('VALUE_MISMATCH'), 1)
        self.assertEqual(record['final']['added_codes'], [])
        self.assertTrue(record['source_untouched'])
        for name, content in before.items():
            self.assertEqual((two / name).read_bytes(), content)

    def test_unfixable_step_rejects_plan_and_restores_candidate(self):
        record_ws = self.root / 'workspace'
        with self.assertRaises(fixes.FixError) as caught:
            fix_plan.run_plan(self.source, self.project,
                              [{'group': self.value_group},
                               {'group': {'id': 'R9', 'issue_ids': []}}],
                              record_ws)
        self.assertIn('No supported finding', str(caught.exception))
        for name, content in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), content)

    def test_plan_snapshots_every_hashed_file(self):
        # run_plan must verify steps against evolving hashes covering the full
        # provenance set: a linked .kicad_pro in source_hashes must be present
        # in the candidate work dir or prepare() rejects every real project.
        linked = self.root / 'linked-plan-project'
        linked.mkdir()
        (linked / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (linked / 'test.kicad_pcb').write_text(board(footprint(value='100nF')))
        (linked / 'test.kicad_pro').write_text('{"meta": {"version": 1}}\n')
        project = analyze_project(linked)
        hashes = dict(project.provenance['source_hashes'])
        hashes['test.kicad_pro'] = fixes.digest((linked / 'test.kicad_pro').read_bytes())
        project.provenance = {'source_hashes': hashes}
        group = next(g for g in project.review['groups'] if any(
            project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        before = {p: (linked / p).read_bytes()
                  for p in ('test.kicad_sch', 'test.kicad_pcb', 'test.kicad_pro')}
        record = fix_plan.run_plan(linked, project, [{'group': group}], self.root / 'workspace')
        self.assertEqual(record['steps'][0]['status'], 'completed')
        work = self.root / 'workspace' / 'candidates' / record['candidate'] / 'work'
        self.assertTrue((work / 'test.kicad_pro').is_file())
        for name, content in before.items():
            self.assertEqual((linked / name).read_bytes(), content)

    def test_failing_step_stops_after_bounded_attempts(self):
        with self.assertRaises(fixes.FixError) as caught:
            fix_plan.run_plan(self.source, self.project,
                              [{'group': {'id': 'R9', 'issue_ids': []}}],
                              self.root / 'workspace')
        self.assertIn('after 2 attempts', str(caught.exception))

    def test_final_validation_gate(self):
        ok = fix_plan.final_validation({'A': 2}, {'A': 1}, {'A'}, set())
        self.assertEqual(ok['target_removed'], {'A': 1})
        with self.assertRaises(fixes.FixError):
            fix_plan.final_validation({'A': 2}, {'A': 2}, {'A'}, set())
        with self.assertRaises(fixes.FixError):
            fix_plan.final_validation({'A': 1}, {'A': 0, 'B': 1}, {'A'}, set())
        with self.assertRaises(fixes.FixError):
            fix_plan.final_validation({'A': 1}, {'A': 0, 'X': 1}, {'A'}, {'X'})
        cleared = fix_plan.final_validation({'A': 1}, {'A': 0}, {'A'}, {'X'})
        self.assertEqual(cleared['expected_cleared'], ['X'])
        staged = fix_plan.final_validation({}, {}, {'KICAD_DRC_UNCONNECTED_ITEMS'}, set(),
                                            staged_resolved={'KICAD_DRC_UNCONNECTED_ITEMS'})
        self.assertEqual(staged['target_removed'],
                         {'KICAD_DRC_UNCONNECTED_ITEMS': 0})
        with self.assertRaises(fixes.FixError):
            fix_plan.final_validation({}, {}, {'KICAD_DRC_UNCONNECTED_ITEMS'}, set())

    def test_move_footprint_step_inside_plan_with_kicad(self):
        home = Path(self.temp.name) / 'kicadhome2'
        home.mkdir()
        move = self.root / 'move-project'
        move.mkdir()
        pcb = PCB_GAP.replace('(segment (start 11 20) (end 12 20) (width .2) (layer "F.Cu") '
                              '(net 1) (uuid "' + TRACK_UUID + '"))\n ', ' ')
        (move / 'test.kicad_pcb').write_bytes(pcb.encode())
        (move / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        hashes = {n: fixes.digest((move / n).read_bytes())
                  for n in ('test.kicad_pcb', 'test.kicad_sch')}
        project = Project('move')
        project.files = [ProjectFile('test.kicad_pcb', 'pcb', len(pcb)),
                         ProjectFile('test.kicad_sch', 'schematic', 10)]
        project.selected_files = {'pcb': 'test.kicad_pcb', 'schematic': 'test.kicad_sch'}
        project.provenance = {'source_hashes': hashes}
        project.components = {}
        project.issues = []
        before = {n: (move / n).read_bytes() for n in hashes}
        ops = [{'kind': 'move_footprint', 'reference': 'U1', 'x_mm': 15, 'y_mm': 20,
                'rotation_deg': 0, 'reason': 'Relocation probe: shift U1 +5mm in x.'}]
        with patch.dict(os.environ, {'HOME': str(home)}):
            record = fix_plan.run_plan(move, project,
                                       [{'group': {'id': 'R1'}, 'operations': ops}],
                                       self.root / 'workspace', kicad_cli=KICAD_CLI)
        self.assertEqual(record['steps'][0]['status'], 'completed')
        self.assertEqual(record['steps'][0]['kind'], 'edit_batch')
        self.assertTrue(record['source_untouched'])
        work_text = (self.root / 'workspace' / 'candidates' / record['candidate'] /
                     'work' / 'test.kicad_pcb').read_text()
        self.assertIn('(at 15 20)', work_text)
        for name, content in before.items():
            self.assertEqual((move / name).read_bytes(), content)

    def test_ground_step_inside_two_step_plan_with_kicad(self):
        home = Path(self.temp.name) / 'kicadhome'
        home.mkdir()
        gap = self.root / 'gap-project'
        gap.mkdir()
        (gap / 'test.kicad_pcb').write_bytes(PCB_GAP.encode())
        (gap / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        hashes = {n: fixes.digest((gap / n).read_bytes())
                  for n in ('test.kicad_pcb', 'test.kicad_sch')}
        project = Project('gap')
        project.files = [ProjectFile('test.kicad_pcb', 'pcb', len(PCB_GAP)),
                         ProjectFile('test.kicad_sch', 'schematic', 10)]
        project.selected_files = {'pcb': 'test.kicad_pcb', 'schematic': 'test.kicad_sch'}
        project.provenance = {'source_hashes': hashes}
        project.components = {'U1': Component('U1', schematic_pins=[Pin('1', net='GND')])}
        project.issues = [Issue('error', 'KICAD_DRC_UNCONNECTED_ITEMS', 'gap',
                                items=[{'uuid': PAD_UUID}, {'uuid': TRACK_UUID}])]
        group = {'id': 'R1', 'issue_ids': ['F0001']}
        before = {n: (gap / n).read_bytes() for n in hashes}
        with patch.dict(os.environ, {'HOME': str(home)}):
            record = fix_plan.run_plan(gap, project, [{'group': group}],
                                       self.root / 'workspace', kicad_cli=KICAD_CLI)
        self.assertEqual(record['steps'][0]['status'], 'completed')
        self.assertEqual(record['steps'][0]['recheck']['method'], 'staged-validation')
        self.assertTrue(record['source_untouched'])
        for name, content in before.items():
            self.assertEqual((gap / name).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
