"""Project-linking regression tests: Fix Design must never lose the project.

Release-blocker coverage: reviews analyzed from uploaded copies must fail
prepare with an actionable message (not a bare 'connect' error), reconnect
must accept a typed folder (native dialogs can fail), and paths containing
spaces must link end to end.
"""
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppError, AppState
from pcb_checker.analysis import analyze_project
from pcb_checker.report import save_report
from test_components import schematic, symbol, board, footprint


def fixture_project(root, stem='test'):
    root.mkdir(parents=True, exist_ok=True)
    (root / f'{stem}.kicad_sch').write_text(schematic(symbol(value='10nF')))
    (root / f'{stem}.kicad_pcb').write_text(board(footprint(value='100nF')))
    (root / f'{stem}.kicad_pro').write_text('{}')
    return analyze_project(root)


class ProjectLinkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'project'
        self.project = fixture_project(self.source)
        self.group = next(g for g in self.project.review['groups'] if any(
            self.project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        self.reports = self.root / 'reports'
        self.state = AppState(str(self.source), self.reports)
        self.addCleanup(self.state.close)
        self.report_id = save_report(self.project, self.reports).name
        self.state.project = self.project
        self.state.report_id = self.report_id
        self.state.store.preference('input:' + self.report_id, {'path': str(self.source)})

    def steps(self):
        return [{'group_id': self.group['id']}]

    def test_upload_review_prepare_names_the_upload_problem(self):
        # The exact user failure: a review analyzed from an uploaded ZIP has
        # no live folder, so prepare must say that plainly with the next step.
        self.state.store.preference('input:' + self.report_id, {'upload_name': 'board.zip'})
        with self.assertRaisesRegex(AppError, r'uploaded copy.*board\.zip'):
            self.state.prepare_fix_design({'report_id': self.report_id, 'steps': self.steps()})

    def test_upload_review_prepare_mentions_how_to_link(self):
        self.state.store.preference('input:' + self.report_id, {'upload_name': 'board.zip'})
        with self.assertRaisesRegex(AppError, r'[Ll]ink.*(folder|Browse|folder path)'):
            self.state.prepare_fix_design({'report_id': self.report_id, 'steps': self.steps()})

    def test_missing_folder_prepare_names_the_saved_path(self):
        self.state.store.preference('input:' + self.report_id, {'path': str(self.root / 'gone')})
        with self.assertRaisesRegex(AppError, r'gone'):
            self.state.prepare_fix_design({'report_id': self.report_id, 'steps': self.steps()})

    def test_reconnect_accepts_typed_folder_with_spaces(self):
        spaced = self.root / 'my board rev 2'
        fixture_project(spaced)
        record = self.state.reconnect_fix_design({'report_id': self.report_id, 'folder': str(spaced)})
        self.assertEqual(record['path'], str(spaced.resolve()))

    def test_reconnect_rejects_wrong_project_files(self):
        other = self.root / 'other board'
        other.mkdir()
        (other / 'other.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (other / 'other.kicad_pcb').write_text(board(footprint(value='100nF')))
        (other / 'other.kicad_pro').write_text('{}')
        with self.assertRaises(AppError):
            self.state.reconnect_fix_design({'report_id': self.report_id, 'folder': str(other)})

    def test_link_names_names_missing_files(self):
        spaced = self.root / 'my board'
        fixture_project(spaced)
        with self.assertRaisesRegex(AppError, r'not in .*my board.*missing\.kicad_sch'):
            self.state.link_local_names(str(spaced), ['test.kicad_pro', 'test.kicad_pcb', 'missing.kicad_sch'])

    def test_link_names_in_spaced_folder(self):
        spaced = self.root / 'wenne board copy'
        fixture_project(spaced, stem='falsch')
        linked = self.state.link_local_names(
            str(spaced), ['falsch.kicad_pro', 'falsch.kicad_pcb', 'falsch.kicad_sch'])
        self.assertEqual(linked['path'], str(spaced.resolve()))
        self.assertEqual(linked['pcb'], 'falsch.kicad_pcb')

    def test_prepare_gate_passes_with_spaced_linked_path(self):
        spaced = self.root / 'wenne board copy'
        fixture_project(spaced, stem='falsch')
        self.state.store.preference('input:' + self.report_id, {'path': str(spaced)})
        record = self.state.prepare_fix_design({'report_id': self.report_id, 'steps': self.steps()})
        try:
            self.assertEqual(record['source'], str(spaced.resolve()))
        finally:
            self.state.fix_design_worker.join(timeout=180)

    def test_saved_review_prepare_survives_reopen(self):
        # Tests B/C/G: after a restart, the saved review reopens and the
        # coordinated fix still prepares against the same live folder.
        self.state.close()
        reopened = AppState(str(self.source), self.reports)
        self.addCleanup(reopened.close)
        reopened.open_report(self.report_id)
        self.assertEqual(reopened.report_id, self.report_id)
        record = reopened.prepare_fix_design({'report_id': self.report_id, 'steps': self.steps()})
        try:
            self.assertEqual(record['source'], str(self.source.resolve()))
        finally:
            reopened.fix_design_worker.join(timeout=180)

    def test_investigation_prepare_on_upload_names_upload(self):
        # The per-finding investigation flow is the other path that hit the
        # user: the source check must fail before any AI call is attempted.
        self.state.store.preference('input:' + self.report_id, {'upload_name': 'board.zip'})
        record = self.state.prepare_file_fix({'report_id': self.report_id, 'group_id': self.group['id'],
                                              'investigate_design': True, 'edit_request': 'Fix the mismatch',
                                              'design_answers': {}})
        self.state.fix_worker.join(timeout=120)
        got = self.state.file_fix(record['id'])
        self.assertIn('uploaded copy', got.get('message', ''))
        self.assertIn('board.zip', got.get('message', ''))

    def test_spaced_repair_round_trip_leaves_originals_untouched(self):
        # Test H on a disposable spaced copy: prepare -> validate -> approve
        # -> export, then reparse the exported project and confirm the fix.
        spaced = self.root / 'wenne board copy'
        proj = fixture_project(spaced)
        before = {name: (spaced / name).read_bytes() for name in
                  ('test.kicad_sch', 'test.kicad_pcb', 'test.kicad_pro')}
        group = next(g for g in proj.review['groups'] if any(
            proj.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        sid = save_report(proj, self.reports).name
        self.state.project = proj
        self.state.report_id = sid
        self.state.store.preference('input:' + sid, {'path': str(spaced)})
        record = self.state.prepare_fix_design(
            {'report_id': sid, 'steps': [{'group_id': group['id']}]})
        self.state.fix_design_worker.join(timeout=180)
        final = self.state.fix_design(record['id'])
        self.assertEqual(final['status'], 'validated', final.get('message'))
        self.assertTrue(final['files_changed'])
        approved = self.state.approve_fix_design({'fix_id': record['id']})
        self.assertEqual(approved['status'], 'approved')
        archive, files = self.state.export_fix_design(record['id'])
        self.assertTrue(files)
        bundle = zipfile.ZipFile(io.BytesIO(archive))
        content = bundle.read('project/test.kicad_pcb')
        self.assertIn(b'"10nF"', content)
        for name, data in before.items():
            self.assertEqual((spaced / name).read_bytes(), data, name + ' changed on disk')
        # Reparse the exported copy: it must analyze cleanly.
        reparsed = self.root / 'reparsed board'
        reparsed.mkdir()
        for name in bundle.namelist():
            if name.startswith('project/'):
                (reparsed / name[len('project/'):]).write_bytes(bundle.read(name))
        recompiled = analyze_project(reparsed)
        self.assertTrue(recompiled.issues is not None)


if __name__ == '__main__':
    unittest.main()
