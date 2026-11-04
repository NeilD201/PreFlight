import io
import json
import unittest
import zipfile
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppState, AppError
from pcb_checker.analysis import analyze_project
from test_components import schematic, symbol, board, footprint


class FixDesignApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'project'
        self.source.mkdir()
        (self.source / 'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (self.source / 'test.kicad_pcb').write_text(board(footprint(value='100nF')))
        self.before = {p: (self.source / p).read_bytes()
                       for p in ('test.kicad_sch', 'test.kicad_pcb')}
        self.project = analyze_project(self.source)
        self.group = next(g for g in self.project.review['groups'] if any(
            self.project.issues[int(i[1:]) - 1].code == 'VALUE_MISMATCH' for i in g['issue_ids']))
        self.reports = Path(self.temp.name) / 'reports'
        self.state = AppState(str(self.source), self.reports)
        self.addCleanup(self.state.close)
        self.state.project = self.project
        self.state.report_id = 'review'
        self.state.store.preference('input:review', {'path': str(self.source)})

    def prepare(self, steps=None):
        record = self.state.prepare_fix_design(
            {'report_id': 'review', 'steps': steps or [{'group_id': self.group['id']}]})
        self.state.fix_design_worker.join(timeout=120)
        return self.state.fix_design(record['id'])

    def test_prepare_validates_and_rejects_bad_requests(self):
        with self.assertRaises(AppError):
            self.state.prepare_fix_design({'report_id': 'other', 'steps': [{'group_id': 'R1'}]})
        with self.assertRaises(AppError):
            self.state.prepare_fix_design({'report_id': 'review', 'steps': []})
        with self.assertRaises(AppError):
            self.state.prepare_fix_design({'report_id': 'review', 'steps': [{'group_id': 'R-nope'}]})

    def test_rejects_no_change_findings(self):
        self.group['no_change_basis'] = 'test'
        try:
            with self.assertRaises(AppError):
                self.state.prepare_fix_design(
                    {'report_id': 'review', 'steps': [{'group_id': self.group['id']}]})
        finally:
            del self.group['no_change_basis']

    def test_prepare_validate_approve_export_round_trip(self):
        record = self.prepare()
        self.assertEqual(record['status'], 'validated')
        self.assertTrue(record['files_changed'])
        # Export requires approval first.
        with self.assertRaises(AppError):
            self.state.export_fix_design(record['id'])
        approved = self.state.approve_fix_design({'fix_id': record['id']})
        self.assertEqual(approved['status'], 'approved')
        # The UI follows up with GET /api/fix-design/<id>; the approve
        # response must carry the record id or the follow-up 404s.
        self.assertEqual(approved['id'], record['id'])
        archive, files = self.state.export_fix_design(record['id'])
        self.assertTrue(files)
        bundle = zipfile.ZipFile(io.BytesIO(archive))
        names = bundle.namelist()
        self.assertIn('project/test.kicad_pcb', names)
        content = bundle.read('project/test.kicad_pcb')
        self.assertIn(b'"10nF"', content)
        for name, data in self.before.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_reject_restores_and_blocks_export(self):
        record = self.prepare()
        rejected = self.state.reject_fix_design({'fix_id': record['id'], 'reason': 'not convinced'})
        self.assertEqual(rejected['status'], 'rejected')
        with self.assertRaises(AppError):
            self.state.export_fix_design(record['id'])

    def test_tampered_candidate_blocks_export(self):
        record = self.prepare()
        self.state.approve_fix_design({'fix_id': record['id']})
        work = self.reports / '.fix-design' / record['id'] / 'workspace' / 'candidates' / \
            record['candidate'] / 'work' / 'test.kicad_pcb'
        work.write_bytes(b'tampered')
        with self.assertRaises(AppError):
            self.state.export_fix_design(record['id'])

    def test_prepare_reuses_validated_record_for_identical_steps(self):
        first = self.prepare()
        self.assertEqual(first['status'], 'validated')
        # A retry of the same steps (e.g. after a client timeout) reuses the
        # finished preparation instead of forking duplicate KiCad work.
        again = self.state.prepare_fix_design(
            {'report_id': 'review', 'steps': [{'group_id': self.group['id']}]})
        self.assertEqual(again['id'], first['id'])

    def test_prepare_reuses_inflight_record(self):
        first = self.prepare()
        self.state.fix_designs['stale'] = dict(first, id='stale', status='preparing')
        try:
            again = self.state.prepare_fix_design(
                {'report_id': 'review', 'steps': [{'group_id': self.group['id']}]})
            self.assertEqual(again['id'], first['id'])
        finally:
            del self.state.fix_designs['stale']

    def test_incident_records_are_redacted_and_persisted(self):
        incident = self.state._record_incident('/api/fix-design/prepare', ValueError('secret-key-material'), method='POST')
        path = self.reports/'.incidents'/(incident+'.json')
        saved = json.loads(path.read_text())
        self.assertEqual(saved['incident'], incident)
        self.assertEqual(saved['exception'], 'ValueError')
        self.assertEqual(saved['path'], '/api/fix-design/prepare')
        self.assertNotIn('secret-key-material', path.read_text())

    def test_list_fix_designs_scoped_to_report(self):
        self.assertEqual(self.state.list_fix_designs('review'), {'fixes': []})
        first = self.prepare()
        self.state.reject_fix_design({'fix_id': first['id'], 'reason': 'test'})
        second = self.prepare()
        listed = self.state.list_fix_designs('review')
        # Newest prepared plan first so the UI resumes the latest work.
        self.assertEqual([i['id'] for i in listed['fixes']],
                         [second['id'], first['id']])
        item = listed['fixes'][0]
        self.assertEqual(item['status'], 'validated')
        self.assertIn(self.group['id'], item['group_ids'])
        self.assertTrue(item['files_changed'])
        with self.assertRaises(AppError):
            self.state.list_fix_designs('other')

    def test_records_persist_across_restart(self):
        record = self.prepare()
        self.state.approve_fix_design({'fix_id': record['id']})
        second = AppState(str(self.source), self.reports)
        try:
            loaded = second.fix_design(record['id'])
        finally:
            second.close()
        self.assertEqual(loaded['status'], 'approved')
        self.assertEqual(loaded['candidate'], record['candidate'])


if __name__ == '__main__':
    unittest.main()
