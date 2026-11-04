import json
import unittest
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import file_fixes as fixes
from pcb_checker import fix_candidate
from pcb_checker import fix_decision as fd
from pcb_checker.analysis import analyze_project
from test_components import schematic, symbol, board, footprint


class FixDecisionTests(unittest.TestCase):
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

    def fixed(self, label='fix'):
        wsroot = self.root / label
        record = fix_candidate.run_single_fix(self.source, self.project, self.group, wsroot)
        return wsroot, record['candidate']

    def test_preview_shows_changes_and_checks(self):
        wsroot, cx = self.fixed()
        preview = fd.preview_record(wsroot, cx)
        self.assertEqual(preview['files_changed'], ['test.kicad_pcb'])
        self.assertIn('10nF', preview['diffs']['test.kicad_pcb'])
        self.assertEqual(preview['decision'], {'state': 'undecided'})

    def test_approve_then_export_produces_separate_project(self):
        wsroot, cx = self.fixed()
        decision = fd.approve(wsroot, cx, by='test-user')
        self.assertEqual(decision['state'], 'approved')
        self.assertEqual(fd.current_decision(wsroot, cx)['state'], 'approved')
        exported = fd.export_approved(wsroot, cx, self.root / 'corrected')
        content = (Path(exported['dest']) / 'project' / 'test.kicad_pcb').read_bytes()
        self.assertIn(b'"10nF"', content)
        self.assertNotIn(b'"100nF"', content)
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_export_without_approval_refused(self):
        wsroot, cx = self.fixed()
        with self.assertRaises(fixes.FixError):
            fd.export_approved(wsroot, cx, self.root / 'corrected')

    def test_reject_blocks_export_and_restores_candidate(self):
        wsroot, cx = self.fixed()
        fd.reject(wsroot, cx, 'not convinced')
        self.assertEqual(fd.current_decision(wsroot, cx)['state'], 'rejected')
        with self.assertRaises(fixes.FixError):
            fd.export_approved(wsroot, cx, self.root / 'corrected')
        work = wsroot / 'candidates' / cx / 'work'
        self.assertEqual((work / 'test.kicad_pcb').read_bytes(), self.before['test.kicad_pcb'])
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_post_approval_tamper_blocks_export(self):
        wsroot, cx = self.fixed()
        fd.approve(wsroot, cx)
        work = wsroot / 'candidates' / cx / 'work'
        (work / 'test.kicad_pcb').write_bytes(b'tampered')
        with self.assertRaises(fixes.FixError):
            fd.export_approved(wsroot, cx, self.root / 'corrected')

    def test_state_survives_reload(self):
        wsroot, cx = self.fixed('first')
        fd.approve(wsroot, cx, by='test-user')
        reread = fd.current_decision(wsroot, cx)
        self.assertEqual((reread['state'], reread['by']), ('approved', 'test-user'))
        preview = fd.preview_record(wsroot, cx)
        self.assertEqual(preview['decision']['state'], 'approved')
        exported = fd.export_approved(wsroot, cx, self.root / 'corrected')
        self.assertEqual(exported['files'], ['test.kicad_pcb'])


if __name__ == '__main__':
    unittest.main()
