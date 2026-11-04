"""Durable notes, revision comparisons, indexed history, and reproducible exports."""

import copy
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.storage import RevisionConflict, WorkspaceStore
from pcb_checker.workflow import compare_runs, keyed_groups, readiness, review_bundle
from pcb_checker.review import build_review
from pcb_checker.webapp import AppState, AppError
from test_review import fixture


def report():
    p=fixture();p.review=build_review(p);p.checks_performed=['kicad_drc','pin_net_assignment_comparison']
    p.provenance={'project_name':'Watch','schematic_uuid':'root'}
    return asdict(p)


class WorkflowTests(unittest.TestCase):
    def test_failed_report_save_does_not_publish_partial_results(self):
        from pcb_checker.report import save_report
        p=fixture();p.review=build_review(p)
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.report.html_report',side_effect=ValueError('render failed')):
            with self.assertRaises(ValueError):save_report(p,td)
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_finding_identity_survives_renumbering_and_source_line_changes(self):
        data=report();before=keyed_groups(data)[0]
        changed=copy.deepcopy(data)
        changed['review']['groups'][0]['id']='R090'
        changed['issues'][0]['evidence']=['new-source.kicad_pcb:2000']
        after=keyed_groups(changed)[0]
        self.assertEqual(before['key'],after['key']);self.assertEqual(before['signature'],after['signature'])

    def test_changed_evidence_is_detected_and_prior_decisions_do_not_apply(self):
        data=report();g=keyed_groups(data)[0]
        notes={g['key']:{'status':'reviewed','signature':g['signature']}}
        self.assertEqual(readiness(data,notes)['open'],0)
        changed=copy.deepcopy(data);changed['review']['groups'][0]['summary']='Pad now connects to a different rail.'
        self.assertEqual(readiness(changed,notes)['open'],1)
        self.assertEqual(len(compare_runs(data,changed)['groups']['changed']),1)

    def test_lost_coverage_is_not_treated_as_a_fix(self):
        before=report();after=copy.deepcopy(before);after['review']['groups']=[];after['checks_performed']=[]
        diff=compare_runs(before,after)
        self.assertFalse(diff['coverage_comparable']);self.assertTrue(diff['lost_checks'])
        self.assertEqual(len(diff['groups']['no_longer_reported']),1)
        self.assertIn('must not be treated as fixes',diff['warning'])

    def test_component_placement_and_net_changes(self):
        a=report();b=copy.deepcopy(a)
        b['components']['U42']['pcb'][0]['x_mm']=42.5
        b['schematic_nets']['GND'].remove('U42.6')
        diff=compare_runs(a,b)
        self.assertEqual(diff['components']['changed'][0]['reference'],'U42')
        self.assertEqual(diff['nets'][0]['name'],'GND')

    def test_bundle_checksums_and_csv_formula_escaping(self):
        data=report();data['assembly']={'parts':[{'reference':'R1','value':'=HYPERLINK("bad")','population':'fitted','status':'needs_part_number'}]}
        data['review']['groups'][0]['title']='=cmd'
        content=review_bundle(data,{},[],b'<html>test</html>')
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            manifest=json.loads(z.read('manifest.json'))
            for name,expected in manifest['files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),expected)
            self.assertIn("'=",z.read('parts-review.csv').decode('utf-8-sig'))
            self.assertIn('assembly drafts',z.read('READ-ME.txt').decode())
            self.assertNotIn('api_key',z.read('report.json').decode())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=WorkspaceStore(self.root)
        self.data=report();self.store.index('run-1',self.data,mtime_ns=1,created='2026-09-30T10:00:00+00:00')
        self.group=keyed_groups(self.data)[0]
    def tearDown(self):self.temp.cleanup()

    def annotate(self,**changes):
        data={'run_id':'run-1','key':self.group['key'],'signature':self.group['signature'],'status':'reviewed','note':'Reviewed the documented thermal path.'}
        return self.store.annotate(**(data|changes))

    def test_notes_persist_after_restart_and_are_scoped_to_the_run(self):
        self.annotate();fresh=WorkspaceStore(self.root)
        self.assertEqual(fresh.decisions('run-1')[self.group['key']]['status'],'reviewed')
        fresh.index('run-2',self.data,mtime_ns=2)
        self.assertFalse(fresh.decisions('run-2'))
        self.assertEqual(len(fresh.events('run-1')),1)

    def test_stale_writer_cannot_overwrite_a_decision(self):
        self.annotate()
        with self.assertRaises(RevisionConflict):self.annotate(note='stale overwrite')
        self.annotate(status='open',note='',expected_revision=1)
        self.assertEqual(len(self.store.events('run-1')),2)

    def test_non_open_decisions_require_a_reason(self):
        for status,note in [('reviewed',''),('accepted_risk','  '),('resolved','pretend repair')]:
            with self.assertRaises(ValueError):self.annotate(status=status,note=note)

    def test_indexed_library_paginates_many_runs_and_literal_search(self):
        for i in range(135):self.store.index(f'run-{i+2}',self.data,mtime_ns=i+2,created=f'2026-09-30T11:{i//60:02}:{i%60:02}+00:00')
        first=self.store.library(limit=30);second=self.store.library(limit=30,offset=30)
        self.assertEqual(first['total'],136);self.assertEqual(len(first['items']),30)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertEqual(self.store.library(query='%')['total'],0)
        self.assertEqual(self.store.library(project='watch')['total'],136)

    def test_dashboard_groups_versions_but_keeps_distinct_schematic_projects(self):
        self.store.index('run-new',self.data,mtime_ns=2,created='2026-10-01T10:00:00+00:00')
        other=copy.deepcopy(self.data);other['provenance']['schematic_uuid']='another-board'
        self.store.index('different-board',other,mtime_ns=3,created='2026-10-02T10:00:00+00:00')
        page=self.store.library(latest=True,limit=1)
        self.assertEqual(page['total'],2);self.assertTrue(page['grouped'])
        self.assertEqual(page['items'][0]['id'],'different-board')
        next_page=self.store.library(latest=True,limit=1,offset=1)
        self.assertEqual(next_page['items'][0]['id'],'run-new')
        self.assertEqual(next_page['items'][0]['version_count'],2)
        self.assertEqual(self.store.library()['total'],3)


class AppWorkflowTests(unittest.TestCase):
    def test_saved_run_reopens_after_restart_without_persisting_key(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=fixture();p.review=build_review(p);p.provenance={'project_name':'Watch','schematic_uuid':'root'}
            from pcb_checker.report import save_report
            saved=save_report(p,root)
            state=AppState('unused',root,key='temporary-secret')
            state.open_report(saved.name);g=state.project_data()['review']['groups'][0]
            state.annotate({'report_id':saved.name,'key':g['key'],'status':'accepted_risk','note':'Test note; acceptance is not sign-off.','revision':0})
            state.close()
            fresh=AppState('unused',root)
            self.assertEqual(fresh.report_id,saved.name)
            self.assertEqual(fresh.project_data()['workspace']['readiness']['accepted_risk'],1)
            self.assertFalse(fresh.config()['key_configured'])
            self.assertNotIn(b'temporary-secret',(root/'.preflight.sqlite3').read_bytes())
            fresh.close()

    def test_wrong_project_and_root_identity_comparisons_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            from pcb_checker.report import save_report
            p=fixture();p.review=build_review(p);p.provenance={'project_name':'Watch','schematic_uuid':'root-a'}
            a=save_report(p,td);p.provenance['schematic_uuid']='root-b';b=save_report(p,td)
            state=AppState('',td)
            with self.assertRaises(AppError):state.compare({'before':a.name,'after':b.name})
            with self.assertRaises(AppError):state.compare({'before':a.name,'after':a.name})
            state.close()


if __name__=='__main__':unittest.main()

class RevisionProgressTests(unittest.TestCase):
    def versions(self):
        before=report()
        before['provenance']['source_hashes']={'board.kicad_pcb':'old'}
        after=copy.deepcopy(before)
        after['provenance']['source_hashes']={'board.kicad_pcb':'new'}
        after['review']['groups']=[]
        after['issues']=[]
        return before,after

    def test_changed_files_with_same_checks_clear_item(self):
        before,after=self.versions()
        result=compare_runs(before,after)['progress']
        self.assertEqual(result['cleared'],1)
        self.assertEqual(result['items'][0]['status'],'cleared')

    def test_skipped_checks_unchanged_files_and_removed_parts_cannot_clear(self):
        for change in ('checks','hashes','parts','identity','connectivity'):
            with self.subTest(change=change):
                before,after=self.versions()
                if change=='checks':after['checks_performed']=[]
                if change=='hashes':after['provenance']['source_hashes']=before['provenance']['source_hashes']
                if change=='parts':after['components']={}
                if change=='identity':after['provenance']['schematic_uuid']='different'
                if change=='connectivity':after['connectivity']['status']='failed'
                progress=compare_runs(before,after)['progress']
                self.assertEqual(progress['cleared'],0)
                self.assertEqual(progress['unverified'],1)

    def test_reappearing_problem_is_new_again(self):
        before,after=self.versions()
        result=compare_runs(after,before)['progress']
        self.assertEqual(result['new'],1)
        self.assertEqual(result['cleared'],0)

    def test_reworded_issue_stays_open(self):
        before,after=self.versions()
        after['review']['groups']=copy.deepcopy(before['review']['groups'])
        after['issues']=copy.deepcopy(before['issues'])
        after['review']['groups'][0]['title']='A simpler title for the same problem'
        progress=compare_runs(before,after)['progress']
        self.assertEqual(progress['remaining'],1)
        self.assertEqual(progress['new'],0)
        self.assertEqual(progress['cleared'],0)

    def test_regrouping_raw_warning_does_not_mark_it_fixed(self):
        before,after=self.versions()
        after['issues']=copy.deepcopy(before['issues'])
        progress=compare_runs(before,after)['progress']
        self.assertEqual(progress['cleared'],0)
        self.assertEqual(progress['unverified'],1)
