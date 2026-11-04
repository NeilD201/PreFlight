import os
import unittest
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import file_fixes as fixes
from pcb_checker import fix_candidate as fc
from pcb_checker import fix_workspace as ws
from pcb_checker.analysis import analyze_project
from pcb_checker.models import Component, Issue, Pin, Project, ProjectFile
from test_components import schematic, symbol, board, footprint

PAD_UUID = '11111111-1111-4111-8111-111111111111'
TRACK_UUID = '22222222-2222-4222-8222-222222222222'
PCB_GAP = '''(kicad_pcb (version 20241229)
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal)) (net 1 "GND")
 (footprint "Test" (at 10 20) (layer "F.Cu") (uuid "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa") (property "Reference" "U1")
  (pad "1" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND") (uuid "''' + PAD_UUID + '''")))
 (segment (start 11 20) (end 12 20) (width .2) (layer "F.Cu") (net 1) (uuid "''' + TRACK_UUID + '''"))
 (gr_rect (start 0 0) (end 30 30) (stroke (width 0.1) (type solid)) (fill none)
  (layer "Edge.Cuts") (tstamp "33333333-3333-4333-8333-333333333333") (uuid "33333333-3333-4333-8333-333333333333")))'''

KICAD_CLI = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'


class FixCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'project'
        self.source.mkdir()
        (self.source / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (self.source / 'test.kicad_pcb').write_text(board(footprint(value='100nF')))
        self.project = analyze_project(self.source)
        self.group = next(g for g in self.project.review['groups'] if any(
            self.project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        self.before = {p: (self.source / p).read_bytes()
                       for p in ('test.kicad_sch', 'test.kicad_pcb')}

    def run_fix(self, **kwargs):
        return fc.run_single_fix(self.source, self.project, self.group,
                                 self.root / 'workspace', **kwargs)

    def test_value_fix_end_to_end_on_isolated_candidate(self):
        record = self.run_fix()
        self.assertEqual(record['plan']['kind'], 'value')
        self.assertEqual(record['plan']['status'], 'applied')
        self.assertTrue(record['source_untouched'])
        self.assertEqual(record['recheck']['target_removed'].get('VALUE_MISMATCH'), 1)
        self.assertEqual(record['recheck']['added_codes'], [])
        self.assertTrue(record['recheck']['checks'])
        changed = [r for r, d in record['diff'].items() if d['changed']]
        self.assertEqual(changed, ['test.kicad_pcb'])
        work = self.root / 'workspace' / 'candidates' / record['candidate'] / 'work'
        updated = analyze_project(work)
        self.assertFalse(any(i.code == 'VALUE_MISMATCH' for i in updated.issues))
        for rel, content in self.before.items():
            self.assertEqual((self.source / rel).read_bytes(), content)
        events = [e.split('"event": "')[1].split('"')[0] for e in
                  (self.root / 'workspace' / 'candidates' / record['candidate'] /
                   'transactions.jsonl').read_text().splitlines()]
        self.assertEqual(events, ['candidate_created', 'fix_preparing', 'fix_prepared',
                                  'fix_applied_to_candidate', 'recheck_completed',
                                  'candidate_validated'])

    def test_linked_project_file_present_in_hashes_is_snapshotted(self):
        # Real linked projects carry test.kicad_pro in provenance source_hashes;
        # prepare() verifies the candidate work dir against those hashes, so the
        # snapshot must contain every hashed file, not just pcb+schematic.
        linked = self.root / 'linked-project'
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
        record = fc.run_single_fix(linked, project, group, self.root / 'workspace')
        self.assertEqual(record['plan']['status'], 'applied')
        work = self.root / 'workspace' / 'candidates' / record['candidate'] / 'work'
        self.assertTrue((work / 'test.kicad_pro').is_file())
        self.assertTrue(record['source_untouched'])

    def test_unsupported_finding_rejected_without_touching_source(self):
        self.project.issues = [i for i in self.project.issues if i.code != 'VALUE_MISMATCH']
        group = {'id': 'R9', 'issue_ids': []}
        with self.assertRaises(fixes.FixError):
            fc.run_single_fix(self.source, self.project, group, self.root / 'workspace')
        for rel, content in self.before.items():
            self.assertEqual((self.source / rel).read_bytes(), content)

    def test_rollback_available_after_successful_fix(self):
        record = self.run_fix()
        ws.rollback_candidate(self.root / 'workspace', record['candidate'])
        work = self.root / 'workspace' / 'candidates' / record['candidate'] / 'work'
        self.assertEqual((work / 'test.kicad_pcb').read_bytes(), self.before['test.kicad_pcb'])

    def test_ground_connection_fix_validated_by_real_kicad_drc(self):
        home = Path(self.temp.name) / 'kicadhome'
        home.mkdir()
        source = self.root / 'gap-project'
        source.mkdir()
        (source / 'test.kicad_pcb').write_bytes(PCB_GAP.encode())
        (source / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        hashes = {name: fixes.digest((source / name).read_bytes())
                  for name in ('test.kicad_pcb', 'test.kicad_sch')}
        project = Project('gap')
        project.files = [ProjectFile('test.kicad_pcb', 'pcb', len(PCB_GAP)),
                         ProjectFile('test.kicad_sch', 'schematic', 10)]
        project.selected_files = {'pcb': 'test.kicad_pcb', 'schematic': 'test.kicad_sch'}
        project.provenance = {'source_hashes': hashes}
        project.components = {'U1': Component('U1', schematic_pins=[Pin('1', net='GND')])}
        project.issues = [Issue('error', 'KICAD_DRC_UNCONNECTED_ITEMS', 'gap',
                                items=[{'uuid': PAD_UUID}, {'uuid': TRACK_UUID}])]
        group = {'id': 'R1', 'issue_ids': ['F0001']}
        before = {n: (source / n).read_bytes() for n in hashes}
        with patch.dict(os.environ, {'HOME': str(home)}):
            record = fc.run_single_fix(source, project, group, self.root / 'workspace',
                                       kicad_cli=KICAD_CLI)
        self.assertEqual(record['plan']['kind'], 'ground_connection')
        self.assertTrue(record['plan']['checks'])
        self.assertEqual(record['recheck']['method'], 'staged-validation')
        self.assertTrue(any('DRC on both copies' in c for c in record['recheck']['checks']))
        self.assertTrue(record['source_untouched'])
        for name, content in before.items():
            self.assertEqual((source / name).read_bytes(), content)


if __name__ == '__main__':
    unittest.main()
