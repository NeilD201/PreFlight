"""Exercise real HTTP routes, asynchronous jobs, saved reports and secret boundaries."""

from dataclasses import asdict
import http.client
import io
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppError, AppState, create_server, restore_project, sync_ai_summary, _cache_busted_html, ASSET_TAG
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.__main__ import main
from pcb_checker.review import build_review
from test_review import fixture
from test_components import schematic, symbol, board, footprint


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'project'
        self.source.mkdir()
        (self.source/'example.kicad_sch').write_text(schematic(symbol()))
        (self.source/'example.kicad_pcb').write_text(board(footprint()))
        self.state = AppState(self.source, self.root/'reports')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def finish(self):
        # These jobs export previews through native KiCad subprocesses, whose
        # own bounded runtime can exceed ten seconds on a busy machine.
        self.state.worker.join(timeout=45)
        self.assertFalse(self.state.worker.is_alive(), self.state.snapshot().get('job'))
        return self.state.snapshot()['job']

    def test_repaired_ai_summary_uses_final_validated_coverage(self):
        ai={'status':'completed','coverage':{'validated_actions':3,'requested_actions':3},
            'result':{'summary':'Evidence checks accepted AI guidance for 1 of 3 issues. Other issues keep local check guidance.'}}
        sync_ai_summary(ai)
        self.assertEqual(ai['result']['summary'],'Evidence checks accepted AI guidance for all 3 issues.')

    def test_local_file_links_persist_and_read_saved_changes(self):
        pro=self.source/'example.kicad_pro';pro.write_text('{}')
        paths=[str(self.source/name) for name in ('example.kicad_sch','example.kicad_pcb','example.kicad_pro')]
        linked=self.state.link_local_files(paths)
        self.assertEqual(linked['files']['.kicad_pro'],str(pro.resolve()))
        self.assertEqual(linked['name'],'project',
                         'The linked display name must be the project folder, not the .kicad_pro stem.')
        restarted=AppState(self.source,self.root/'reports')
        try:self.assertEqual(restarted.config()['linked_files'],linked)
        finally:restarted.close()
        self.state.start('analysis',{'linked_files':True,'native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        before=self.state.project.provenance['source_hashes'].copy()
        self.assertFalse(self.state.editor_status()['connections'][0]['review_outdated'])
        sch=self.source/'example.kicad_sch';sch.write_text(sch.read_text()+'\n')
        self.state.editor_connections.scan();self.state.editor_connections.scan()
        self.assertTrue(self.state.editor_status()['connections'][0]['review_outdated'])
        self.state.start('analysis',{'linked_files':True,'native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        self.assertNotEqual(before,self.state.project.provenance['source_hashes'])
        self.assertFalse(self.state.editor_status()['connections'][0]['review_outdated'])
        pro.unlink()
        with self.assertRaises(AppError):self.state.start('analysis',{'linked_files':True,'native':False})

    def test_local_file_link_rejects_incomplete_selection(self):
        with self.assertRaises(AppError):self.state.link_local_files([str(self.source/'example.kicad_sch')])

    def test_link_local_names_autolinks_saved_route_without_dialog(self):
        pro=self.source/'example.kicad_pro';pro.write_text('{}')
        linked=self.state.link_local_names(str(self.source),['example.kicad_pro','example.kicad_pcb','example.kicad_sch'])
        self.assertEqual(linked['path'],str(self.source.resolve()))
        # Second call with no directory reuses the saved route.
        again=self.state.link_local_names(None,['example.kicad_pro','example.kicad_pcb','example.kicad_sch'])
        self.assertEqual(again['path'],linked['path'])
        # Whole-folder glob links the same set with no names at all.
        whole=self.state.link_local_names(str(self.source),None)
        self.assertEqual(whole['files']['.kicad_pro'],linked['files']['.kicad_pro'])

    def test_link_local_names_rejects_unknown_route_and_escapes(self):
        pro=self.source/'example.kicad_pro';pro.write_text('{}')
        with self.assertRaises(AppError):
            self.state.link_local_names(str(self.source),['example.kicad_pro','missing.kicad_sch','example.kicad_pcb'])
        with self.assertRaises(AppError):
            self.state.link_local_names(str(self.source),['../outside.kicad_sch','example.kicad_pro','example.kicad_pcb'])
        fresh=tempfile.TemporaryDirectory();self.addCleanup(fresh.cleanup)
        with self.assertRaises(AppError):
            AppState(Path(fresh.name)/'proj',Path(fresh.name)/'rep').link_local_names(None,['a.kicad_pro'])

    def test_local_file_link_accepts_hierarchical_sheets(self):
        root=self.source/'board.kicad_sch'
        child=self.source/'psu.kicad_sch'
        root.write_text('(kicad_sch (version 20250101) (uuid "aaaaaaaa-0000-0000-0000-000000000001")'
                        ' (sheet (at 10 10) (size 80 50) (uuid "bbbbbbbb-0000-0000-0000-000000000002")'
                        ' (property "Sheetname" "PSU") (property "Sheetfile" "psu.kicad_sch")))')
        child.write_text('(kicad_sch (version 20250101) (uuid "bbbbbbbb-0000-0000-0000-000000000002"))')
        (self.source/'board.kicad_pcb').write_text('(kicad_pcb (version 20241229))')
        (self.source/'board.kicad_pro').write_text('{}')
        paths=[str(self.source/name) for name in ('board.kicad_sch','psu.kicad_sch','board.kicad_pcb','board.kicad_pro')]
        linked=self.state.link_local_files(paths)
        self.assertEqual(linked['schematic'],'board.kicad_sch')
        self.assertEqual(sorted(linked['sheets']),['board.kicad_sch','psu.kicad_sch'])
        self.assertEqual(linked['files']['.kicad_pro'],str((self.source/'board.kicad_pro').resolve()))

    def test_local_file_link_resolves_hierarchy_root_without_pro_stem(self):
        root=self.source/'top.kicad_sch'
        child=self.source/'sub.kicad_sch'
        root.write_text('(kicad_sch (version 20250101) (uuid "aaaaaaaa-0000-0000-0000-000000000001")'
                        ' (sheet (at 10 10) (size 80 50) (uuid "bbbbbbbb-0000-0000-0000-000000000002")'
                        ' (property "Sheetname" "S") (property "Sheetfile" "sub.kicad_sch")))')
        child.write_text('(kicad_sch (version 20250101) (uuid "bbbbbbbb-0000-0000-0000-000000000002"))')
        (self.source/'board.kicad_pcb').write_text('(kicad_pcb (version 20241229))')
        (self.source/'board.kicad_pro').write_text('{}')
        paths=[str(self.source/name) for name in ('top.kicad_sch','sub.kicad_sch','board.kicad_pcb','board.kicad_pro')]
        linked=self.state.link_local_files(paths)
        self.assertEqual(linked['schematic'],'top.kicad_sch')

    def test_local_file_link_rejects_ambiguous_roots(self):
        for name in ('a.kicad_sch','b.kicad_sch'):
            (self.source/name).write_text('(kicad_sch (version 20250101))')
        (self.source/'board.kicad_pcb').write_text('(kicad_pcb (version 20241229))')
        (self.source/'board.kicad_pro').write_text('{}')
        paths=[str(self.source/name) for name in ('a.kicad_sch','b.kicad_sch','board.kicad_pcb','board.kicad_pro')]
        with self.assertRaisesRegex(AppError,'top-level schematic'):
            self.state.link_local_files(paths)

    def test_project_files_upload_names_missing_suffix(self):
        files=[{'name':'board.kicad_pro','text':'{}'},{'name':'board.kicad_sch','text':'{}'},{'name':'board.kicad_dru','text':'{}'}]
        with self.assertRaisesRegex(AppError,r'Missing \.kicad_pcb file'):
            self.state.upload_project_files({'files':files})

    def test_selected_files_upload_and_latest_revision(self):
        files=[{'name':p.name,'text':p.read_text()} for p in self.source.iterdir()]
        files.append({'name':'example.kicad_pro','text':'{}'})
        result=self.state.upload_project_files({'files':files})
        old=self.state.upload['path']
        with zipfile.ZipFile(old) as archive:
            self.assertEqual(set(archive.namelist()),{f['name'] for f in files})
        self.state.start('analysis',{'upload_id':result['id'],'native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        files[-1]['text']='{"updated": true}'
        newer=self.state.upload_project_files({'files':files})
        self.assertNotEqual(result['id'],newer['id']);self.assertFalse(old.exists())
        with zipfile.ZipFile(self.state.upload['path']) as archive:
            self.assertEqual(archive.read('example.kicad_pro'),b'{"updated": true}')
        for bad in [files[:2],files+[files[0]],[{**f,'name':'../'+f['name']} for f in files]]:
            with self.assertRaises(AppError):self.state.upload_project_files({'files':bad})
        self.assertEqual(self.state.upload['id'],newer['id'])

    def test_real_analysis_saves_report_and_can_be_reopened(self):
        before = {p.name:p.read_bytes() for p in self.source.iterdir()}
        self.state.start('analysis', {'path':str(self.source),'native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        data = self.state.project_data()
        self.assertIn('C1',data['components'])
        self.assertEqual(data['ai_review']['status'],'disabled')
        self.assertNotIn('native_reports',data)
        self.assertEqual(data['workspace']['input_source']['path'],str(self.source.resolve()))
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.source.iterdir()})
        history = self.state.history()
        self.assertEqual(len(history),1)
        self.assertTrue(self.state.report_path(history[0]['id'],'report.html').is_file())
        original = evidence_packet(self.state.project)
        self.state.project = None
        self.state.open_report(history[0]['id'])
        self.assertEqual(evidence_packet(self.state.project),original)
        self.assertEqual(self.state.project_data()['workspace']['input_source']['path'],str(self.source.resolve()))
        (self.source/'example.kicad_pcb').write_text(board(footprint(value='22u')))
        current=self.state.project_data()
        self.assertEqual(current['workspace']['source_status']['status'],'changed')
        self.assertEqual(current['ai_review']['status'],'stale')

    def test_updated_zip_keeps_baseline_and_saves_automatic_progress(self):
        request={'path':str(self.source),'native':False,'datasheets':False,'project_name':'My board'}
        self.state.start('analysis',request);self.assertEqual(self.finish()['status'],'completed')
        baseline=self.state.report_id
        (self.source/'example.kicad_pcb').write_text(board(footprint(value='22u')))
        archive=self.root/'updated.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for path in self.source.iterdir():z.write(path,path.name)
        self.state.start('analysis',{**request,'path':str(archive),'baseline_id':baseline,'project_name':'different zip name'})
        self.assertEqual(self.finish()['status'],'completed')
        result=self.state.read_report(self.state.report_id)
        self.assertEqual(result['provenance']['project_name'],'My board')
        comparison=result['provenance']['revision_comparison']
        self.assertEqual(comparison['before'],baseline)
        self.assertTrue(comparison['progress']['files_changed'])
        self.assertGreater(comparison['progress']['new'],0)
        self.assertTrue(self.state.report_path(baseline).exists())
        saved=self.state.report_id;self.state.open_report(baseline);self.state.open_report(saved)
        self.assertEqual(self.state.project_data()['provenance']['revision_comparison'],comparison)
        # Repair the actual mismatch and run the parser again, rather than deleting a finding.
        (self.source/'example.kicad_pcb').write_text(board(footprint()))
        self.state.start('analysis',{**request,'baseline_id':saved})
        self.assertEqual(self.finish()['status'],'completed')
        progress=self.state.project_data()['provenance']['revision_comparison']['progress']
        self.assertGreater(progress['cleared'],0)
        self.assertEqual(progress['new'],0)


    def test_updated_files_with_different_schematic_are_rejected(self):
        request={'path':str(self.source),'native':False,'datasheets':False}
        self.state.start('analysis',request);self.finish();baseline=self.state.report_id
        path=self.source/'example.kicad_sch'
        path.write_text(path.read_text().replace('(uuid "root")','(uuid "different-board")'))
        self.state.start('analysis',{**request,'baseline_id':baseline})
        result=self.finish()
        self.assertEqual(result['status'],'failed')
        self.assertIn('different schematic',result['message'])
        self.assertEqual(self.state.report_id,baseline)
        self.assertTrue(self.state.report_path(baseline).exists())

    def test_step_help_is_bound_to_current_report_and_works_without_key(self):
        self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False});self.finish()
        action=self.state.project.action_plan['actions'][0]
        request={'report_id':self.state.report_id,'group_id':action['group_id'],'step_index':0,
                 'question':'How do I start?','history':[]}
        self.assertEqual(self.state.step_help(request)['status'],'local')
        self.assertEqual(self.state.step_help({**request,'history':[{'role':'assistant','text':'a'*3000}]})['status'],'local')
        with self.assertRaises(AppError):self.state.step_help({**request,'report_id':'other'})
        with self.assertRaises(AppError):self.state.step_help({**request,'step_index':99})

    def test_project_context_roundtrip_feeds_description(self):
        self.state.start('analysis', {'path': str(self.source), 'native': False, 'datasheets': False})
        self.assertEqual(self.finish()['status'], 'completed')
        report = self.state.report_id
        empty = self.state.project_context({'report_id': report})
        self.assertEqual(empty['description'], '')
        saved = self.state.project_context({'report_id': report, 'description': 'Demo keyboard',
                                            'context_revision': empty['context_revision']})
        self.assertEqual(saved['description'], 'Demo keyboard')
        again = self.state.project_context({'report_id': report})
        self.assertEqual(again['description'], 'Demo keyboard')
        self.assertEqual(again['context_revision'], saved['context_revision'])
        with self.assertRaises(AppError):
            self.state.project_context({'report_id': report, 'description': 'x' * 6001,
                                        'context_revision': saved['context_revision']})

    def test_demo_projects_lists_bundled_sets_only(self):
        demos = self.state.demo_projects()['demos']
        names = [d['name'] for d in demos]
        self.assertIn('ModularKeyboard wrong', names)
        self.assertIn('ModularKeyboard correct', names)
        for demo in demos:
            self.assertTrue(demo['directory'].endswith(demo['name']))
            self.assertEqual(len([f for f in demo['files'] if f.endswith('.kicad_pro')]), 1)
            self.assertEqual(len([f for f in demo['files'] if f.endswith('.kicad_pcb')]), 1)
            self.assertTrue(any(f.endswith('.kicad_sch') for f in demo['files']))
            linked = self.state.link_local_names(demo['directory'], demo['files'])
            self.assertEqual(linked['path'], demo['directory'])
            self.assertIn('preset', demo)
        presets = {d['name']: d['preset'] for d in demos}
        self.assertIn('modular mechanical keyboard', presets['ModularKeyboard wrong'].lower())
        self.assertTrue(presets['ModularKeyboard correct'])

    def test_incident_record_traces_frames_without_leaking_secrets(self):
        try:
            {'key': 'fake-secret-key'}.missing_attribute.deep
        except AttributeError as exc:
            incident = self.state._record_incident('/api/step-help', exc, method='POST')
        record = json.loads((self.root/'reports'/'.incidents'/(incident + '.json')).read_text())
        self.assertEqual(record['exception'], 'AttributeError')
        self.assertEqual(record['path'], '/api/step-help')
        self.assertTrue(record['frames'])
        self.assertTrue(all(set(frame) == {'file', 'line', 'function'} for frame in record['frames']))
        self.assertNotIn('fake-secret-key', json.dumps(record))

    def test_restored_records_preserve_ai_evidence_and_types(self):
        project = fixture(); project.review = build_review(project)
        restored = restore_project(asdict(project))
        self.assertEqual(evidence_packet(restored),evidence_packet(project))
        self.assertEqual(restored.components['U42'].pcb[0].pads[0].number,'6')
        self.assertEqual(restored.components['U42'].schematic_pins[0].net,'GND')

    def test_question_reuses_project_and_saves_new_report_without_kicad(self):
        self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False});self.finish()
        prior = self.state.report_id
        self.state.settings({'key':'local-test-secret'})
        with patch('pcb_checker.webapp.analyze_project') as analyze, patch('pcb_checker.webapp.run_ai_review',return_value={'status':'completed','model':'test-model','result':{'summary':'Test answer','answer':'Inspect the evidence.','findings':[],'questions':[],'limitations':[]}}) as review:
            self.state.start('question',{'question':'What needs attention?'})
            self.assertEqual(self.finish()['status'],'completed')
        analyze.assert_not_called()
        self.assertEqual(review.call_args.kwargs['question'],'What needs attention?')
        self.assertNotEqual(self.state.report_id,prior)
        self.assertEqual(len(self.state.history()),2)
        for p in (self.state.reports/self.state.report_id).iterdir():
            self.assertNotIn(b'local-test-secret',p.read_bytes())

    def test_ai_failure_preserves_local_results(self):
        self.state.settings({'key':'local-test-secret'})
        with patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'HTTP 503; high demand'}):
            self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False,'ai':True})
            self.assertEqual(self.finish()['status'],'completed')
        self.assertIn('C1',self.state.project.components)
        self.assertEqual(self.state.project.ai_review['status'],'failed')
        self.assertTrue(self.state.report_path(self.state.report_id).is_file())

    def test_single_active_job_prevents_settings_upload_or_report_replacement(self):
        entered,release = threading.Event(),threading.Event()
        def delayed(*a,**kw):
            entered.set();release.wait(timeout=5);p=fixture();p.review=build_review(p);return p
        with patch('pcb_checker.webapp.analyze_project',side_effect=delayed):
            self.state.start('analysis',{'path':str(self.source),'datasheets':False})
            self.assertTrue(entered.wait(3))
            try:
                for action in [lambda:self.state.start('analysis',{'path':str(self.source),'datasheets':False}),lambda:self.state.settings({'key':'secret'}),lambda:self.state.open_report('anything')]:
                    with self.assertRaises(AppError) as caught: action()
                    self.assertEqual(caught.exception.status,409)
            finally: release.set();self.finish()

    def test_missing_key_or_question_fails_before_starting(self):
        for kind,data in [('analysis',{'path':str(self.source),'ai':True}),('connection',{}),('question',{'question':'why'})]:
            with self.assertRaises(AppError):self.state.start(kind,data)
        self.assertIsNone(self.state.worker)
        self.state.settings({'key':'secret'})
        with self.assertRaises(AppError):self.state.start('question',{'question':'why'})

    def test_settings_retains_clears_and_never_returns_key(self):
        self.state.settings({'key':'local-test-secret','model':'gemini-3.5-flash-lite'})
        self.state.settings({'key':''})
        self.assertEqual(self.state.key,'local-test-secret')
        self.assertTrue(self.state.config()['key_configured'])
        self.assertNotIn('local-test-secret',json.dumps(self.state.config()))
        self.assertEqual(self.state.config()['app_version'],'v1.04')
        self.assertNotIn('local-test-secret',json.dumps(self.state.snapshot()))
        self.state.settings({'clear_key':True})
        self.assertFalse(self.state.config()['key_configured'])
        for data in [{'key':'gen-lang-client-123'},{'key':'secret\nvalue'},{'model':'../other'}]:
            with self.assertRaises(AppError):self.state.settings(data)

    def test_worker_redacts_exception(self):
        self.state.settings({'key':'local-test-secret'})
        with patch('pcb_checker.webapp.analyze_project',side_effect=ValueError('bad local-test-secret')):
            self.state.start('analysis',{'path':str(self.source),'datasheets':False})
            job=self.finish()
        self.assertEqual(job['status'],'failed')
        self.assertEqual(job['message'],'bad [redacted]')

    def test_connection_probe_reads_no_project(self):
        self.state.settings({'key':'secret'})
        with patch('pcb_checker.webapp.analyze_project') as analyze, patch('pcb_checker.webapp.check_ai_connection',return_value={'status':'failed','message':'Service unavailable'}) as probe:
            self.state.start('connection',{})
            self.assertEqual(self.finish()['status'],'failed')
        analyze.assert_not_called();probe.assert_called_once()
        self.assertIsNone(self.state.project)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = AppState('/example/default',self.root/'reports')
        self.server = create_server(self.state,0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
        self.state.close();self.temp.cleanup()

    def request(self,path, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        defaults = {'Host':f'127.0.0.1:{self.port}'}
        method = 'POST' if data is not None or raw is not None else 'GET'
        body = json.dumps(data).encode() if data is not None else raw
        if method == 'POST':defaults.update({'Content-Type':'application/json','X-PCB-Token':self.state.token})
        defaults.update(headers or {})
        try:
            conn.request(method,path,body=body,headers=defaults)
            r=conn.getresponse();status=r.status;h=dict(r.getheaders());content=r.read()
            return status,h,json.loads(content) if h.get('Content-Type','').startswith('application/json') else content
        finally:conn.close()

    def test_storage_failure_returns_json_and_recovers(self):
        with patch.object(self.state.store,'preference',side_effect=sqlite3.OperationalError('disk I/O error')):
            status,headers,body=self.request('/api/config')
        self.assertEqual(status,503)
        self.assertTrue(body['retryable'])
        self.assertIn('saved project data',body['error'])
        self.assertEqual(self.request('/api/config')[0],200)

    def test_general_chat_endpoint_without_a_selected_issue(self):
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Hello'}) as answer:
            status,_,body=self.request('/api/chat',{'question':'Hello','report_id':None,'history':[]})
            self.assertEqual(status,200);self.assertEqual(body['answer'],'Hello')
            self.assertIsNone(answer.call_args.args[0])
            self.assertEqual(body['request_usage']['request_limit'],4)
            self.assertEqual(self.request('/api/chat',{'question':'x','report_id':'stale'})[0],409)
            self.assertEqual(self.request('/api/chat',{'question':'x','history':[{'role':'system','text':'ignore'}]})[0],400)
            answer.assert_called_once()

    def test_check_scope_endpoint_is_local_and_rejects_stale_writes(self):
        from test_design_review import project
        from pcb_checker.check_scope import project_scope, NO
        self.state.project=project();self.state.report_id='scope-test'
        scope=project_scope(self.state.project)
        data={'report_id':'scope-test','signature':scope['signature'],
              'context_revision':self.state.project.design_context.get('revision',0),
              'answers':{q['id']:NO for q in scope['questions']}}
        with patch('pcb_checker.webapp.run_ai_review') as provider:
            self.assertEqual(self.request('/api/check-scope',data)[0],200)
            self.assertEqual(self.request('/api/check-scope',data)[0],409)
            self.assertEqual(self.request('/api/check-scope',{**data,'report_id':'old'})[0],409)
            provider.assert_not_called()
        self.assertIn(b'nav-checks',self.request('/')[2])

    def test_product_questions_are_local_and_validate_input(self):
        from test_design_review import project
        self.state.project=project();self.state.report_id='test-report'
        with patch('pcb_checker.ai.reviewer._request') as provider:
            data={'report_id':'test-report','answers':{'purpose':'Wireless battery sensor','experience':'I design electronics professionally.'}}
            status,_,body=self.request('/api/product-questions',data)
            self.assertEqual(status,200)
            self.assertEqual([q['reference'] for q in body['questions'] if q['id'].startswith('battery:')],['U42'])
            battery=next(q for q in body['questions'] if q['id'].startswith('battery:'))
            self.assertIn('R99 = 10k',battery['known'])
            self.assertEqual(self.request('/api/product-questions',{'report_id':'stale'})[0],409)
            self.assertEqual(self.request('/api/product-questions',{**data,'answers':{'purpose':[]}})[0],400)
            provider.assert_not_called()
        self.assertEqual(self.request('/product-context.js')[0],200)

    def test_livescan_static_and_interpret_routes(self):
        status, headers, body = self.request('/scanmath.js')
        self.assertEqual(status, 200)
        self.assertIn('text/javascript', headers.get('Content-Type', ''))
        self.assertIn(b'ScanMath', body)



    def test_answer_endpoints_save_without_ai_and_reject_stale_revision(self):
        from test_design_review import project
        from pcb_checker.design_review import context_key
        self.state.project=project();self.state.report_id='memory-test'
        with patch('pcb_checker.ai.reviewer._request') as provider:
            status,_,questions=self.request('/api/product-questions',{'report_id':'memory-test'})
            self.assertEqual(status,200)
            question=questions['questions'][0]
            data={'report_id':'memory-test','context_revision':questions['context_revision'],'answers':{question['id']:'Battery pack not selected yet'}}
            status,_,saved=self.request('/api/product-answers',data)
            self.assertEqual(status,200)
            self.assertNotIn(question['id'],saved['unanswered'])
            self.assertEqual(self.request('/api/product-answers',data)[0],409)
            status,_,answer=self.request('/api/question-answer',{'report_id':'memory-test','context_revision':saved['context_revision'],
                'question':'What enclosure?','answer':'Plastic case'})
            self.assertEqual(status,200)
            provider.assert_not_called()
        context=self.state.store.design_context(context_key(self.state.project))
        self.assertEqual(context['product_context'][question['id']],'Battery pack not selected yet')
        self.assertEqual(next(iter(context['question_answers'].values()))['answer'],'Plastic case')

    def test_answer_seeds_missing_row_for_relinked_project(self):
        from test_design_review import project
        from pcb_checker.design_review import context_key
        self.state.project=project();self.state.report_id='relink-test'
        self.state.project.design_context['revision']=7
        self.assertIsNone(self.state.store.design_context(context_key(self.state.project)))
        status,_,receipt=self.request('/api/question-answer',{'report_id':'relink-test','context_revision':7,
            'question':'Will it be used outdoors?','answer':'Indoor only.'})
        self.assertEqual(status,200)
        context=self.state.store.design_context(context_key(self.state.project))
        self.assertEqual(len(context['question_answers']),1)
        status,_,receipt=self.request('/api/question-answer',{'report_id':'relink-test',
            'context_revision':receipt['revision'],'question':'Will it be used outdoors?','answer':'Indoor only, updated.'})
        self.assertEqual(status,200)
        self.assertEqual(self.request('/api/question-answer',{'report_id':'relink-test','context_revision':1,
            'question':'Will it be used outdoors?','answer':'stale'})[0],409)

    def test_answer_updates_same_slot_across_interview_and_form(self):
        from test_design_review import project
        from pcb_checker.design_review import context_key
        self.state.project=project();self.state.report_id='dup-test'
        self.state.project.ai_review={'status':'completed','result':{'questions':['Will J1 be used outdoors where water can reach the cable?']}}
        status,_,body=self.request('/api/project')
        self.assertEqual(status,200)
        tasks=[t for t in body['review_inputs']['tasks'] if t.get('kind')=='question']
        self.assertTrue(tasks)
        task=tasks[0];rev=body['workspace']['design_context']['revision']
        status,_,first=self.request('/api/question-answer',{'report_id':'dup-test','context_revision':rev,
            'question':task['question'],'answer':'First.','review_task':{'id':task['id'],'signature':task['signature']}})
        self.assertEqual(status,200)
        status,_,second=self.request('/api/question-answer',{'report_id':'dup-test','context_revision':first['revision'],
            'question':task['question'],'answer':'Second.'})
        self.assertEqual(status,200)
        self.assertEqual(second['revision'],first['revision']+1)
        context=self.state.store.design_context(context_key(self.state.project))
        matching=[v for v in context['question_answers'].values() if v.get('question')==task['question']]
        self.assertEqual(len(matching),1)
        self.assertEqual(matching[0]['answer'],'Second.')
        self.assertEqual(matching[0].get('review_task'),{'id':task['id'],'signature':task['signature']})

    def test_unexpected_endpoint_errors_are_json_and_do_not_expose_details(self):
        for path, data, method in [('/api/config', None, 'config'), ('/api/settings', {}, 'settings')]:
            with patch.object(self.state, method, side_effect=KeyError('private-key-and-project-text')):
                status, headers, body = self.request(path, data)
            self.assertEqual(status, 500)
            self.assertTrue(headers['Content-Type'].startswith('application/json'))
            self.assertIn('reference', body['error'])
            self.assertNotIn('private-key', json.dumps(body))
        self.assertEqual(self.request('/api/config')[0], 200)

    def test_assets_config_and_readonly_api(self):
        for path,mime in [('/','text/html'),('/app.js','text/javascript'),('/workflow.js','text/javascript'),('/app.css','text/css')]:
            status,headers,body=self.request(path)
            self.assertEqual(status,200);self.assertTrue(headers['Content-Type'].startswith(mime));self.assertGreater(len(body),100)
            self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        _,_,config=self.request('/api/config')
        self.assertEqual(config['default_path'],'/example/default')
        self.assertFalse(config['key_configured'])
        self.assertIsNone(self.request('/api/project')[2])

    def test_step_help_route_uses_selected_saved_report(self):
        from pcb_checker.report import save_report
        from pcb_checker.design_review import local_plan
        p=fixture();p.review=build_review(p);p.action_plan=local_plan(p)
        folder=save_report(p,self.state.reports)
        self.assertEqual(self.request('/api/open-report',{'id':folder.name})[0],200)
        group_id=p.action_plan['actions'][0]['group_id']
        payload={'report_id':folder.name,'group_id':group_id,'step_index':0,'question':'How?', 'history':[]}
        status,_,body=self.request('/api/step-help',payload)
        self.assertEqual(status,200)
        self.assertEqual(body['status'],'local')
        self.assertEqual(self.request('/api/step-help',{**payload,'group_id':'R999'})[0],400)

    def test_library_decisions_compare_and_export_routes(self):
        from pcb_checker.report import save_report
        p=fixture();p.review=build_review(p);p.provenance={'project_name':'watch','schematic_uuid':'root'}
        first=save_report(p,self.state.reports)
        p.components['U42'].pcb[0].x_mm=15
        second=save_report(p,self.state.reports)
        self.assertEqual(self.request('/api/library/refresh',{})[0],200)
        data=self.request('/api/library?project=watch&limit=1')[2]
        self.assertEqual(data['total'],2);self.assertEqual(len(data['items']),1)
        # Preview availability: legacy runs without the SVG export are
        # announced so the frontend never requests a 404 (console stays
        # clean); runs with the export keep their preview.
        self.assertEqual(data['items'][0]['has_preview'],False)
        (self.state.reports/first.name/'board-front.svg').write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        data=self.request('/api/library?project=watch&limit=2')[2]
        by_id={it['id']:it for it in data['items']}
        self.assertEqual(by_id[first.name]['has_preview'],True)
        self.assertEqual(by_id[second.name]['has_preview'],False)
        self.assertEqual(self.request('/api/library?offset=-1')[0],400)
        self.assertEqual(self.request('/api/open-report',{'id':first.name})[0],200)
        group=self.request('/api/project')[2]['review']['groups'][0]
        note={'report_id':first.name,'key':group['key'],'status':'reviewed','note':'Inspected the source requirement.','revision':0}
        self.assertEqual(self.request('/api/decision',note)[0],200)
        self.assertEqual(self.request('/api/decision',note)[0],409)
        state=self.request('/api/project')[2]['workspace']
        self.assertEqual(state['readiness']['reviewed'],1)
        result=self.request('/api/compare',{'before':first.name,'after':second.name})[2]
        self.assertEqual(result['components']['changed'][0]['reference'],'U42')
        status,headers,body=self.request('/api/bundle/'+first.name)
        self.assertEqual(status,200);self.assertEqual(headers['Content-Type'],'application/zip')
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertIn('review-decisions.json',z.namelist())
            self.assertIn('Inspected the source requirement.',z.read('review-decisions.json').decode())
        status,headers,body=self.request('/api/checklist/'+first.name)
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertIn(b'UNVERIFIED: Supply current budget',body)
        self.assertIn(first.name.encode(),body)
        self.assertEqual(self.request('/api/checklist/..%2F..%2Fsecrets')[0],404)

    def test_cross_origin_and_host_attacks_are_rejected(self):
        for headers in [{'Host':'attacker.example'},{'Origin':'https://attacker.example'},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.request('/api/config',headers=headers)[0],403)
        for headers in [{'X-PCB-Token':''},{'X-PCB-Token':'wrong'},{'Origin':'http://localhost:9999'}]:
            self.assertEqual(self.request('/api/settings',{'key':'secret'},headers=headers)[0],403)
        self.assertFalse(self.state.key)

    def test_editor_connections_preserve_access_controls_and_format_boundary(self):
        source=self.root/'board.brd';source.write_text('<eagle><drawing><board/></drawing></eagle>')
        data={'editor':'eagle','files':[str(source.resolve())]}
        for headers in [{'X-PCB-Token':''},{'Origin':'https://easyeda.com'},{'Host':'attacker.example'}]:
            self.assertEqual(self.request('/api/editor-connect',data,headers=headers)[0],403)
        status,_,record=self.request('/api/editor-connect',data)
        self.assertEqual(status,200)
        status,_,listing=self.request('/api/editor-connections')
        self.assertEqual(status,200)
        self.assertFalse(listing['connections'][0]['review_available'])
        self.assertEqual(self.request('/api/editor-use',{'id':record['id']})[0],409)
        self.assertIsNone(self.state.job)
        self.assertEqual(self.request('/api/editor-disconnect',{'id':record['id']})[0],200)
        self.assertTrue(source.exists())

    def test_mode_only_settings_update_preserves_provider(self):
        self.assertTrue(self.state.model.startswith('meta/'))
        status, _, config = self.request('/api/settings', {'ai_usage_mode': 'complete'})
        self.assertEqual(status, 200)
        self.assertEqual(config['ai_usage_mode'], 'complete')
        self.assertEqual(config['ai_request_limits']['repair'], 20)
        self.assertTrue(self.state.model.startswith('meta/'))
        status, _, config = self.request('/api/settings', {'ai_usage_mode': 'economy'})
        self.assertEqual(status, 200)
        self.assertEqual(config['ai_usage_mode'], 'complete')
        self.assertTrue(self.state.model.startswith('meta/'))

    def test_request_validation(self):
        self.assertEqual(self.request('/api/settings',raw=b'invalid')[0],400)
        self.assertEqual(self.request('/api/settings',raw=b'[]')[0],400)
        self.assertEqual(self.request('/api/settings',raw=b'{}',headers={'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.request('/api/settings',{},headers={'Content-Length':'1000000'})[0],413)
        self.assertEqual(self.request('/api/run',{'path':'/definitely/missing'})[0],400)

    def test_zip_upload_runs_actual_importer(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as z:
            z.writestr('watch/example.kicad_sch',schematic(symbol()))
            z.writestr('watch/example.kicad_pcb',board(footprint()))
        status,_,upload=self.request('/api/upload',raw=data.getvalue(),headers={'X-Filename':'watch.zip','Content-Type':'application/zip'})
        self.assertEqual(status,200)
        path=self.state.upload['path'];self.assertTrue(path.is_file())
        self.assertEqual(self.request('/api/run',{'upload_id':upload['id'],'native':False,'datasheets':False})[0],202)
        self.state.worker.join(10)
        self.assertEqual(self.state.snapshot()['job']['status'],'completed')
        self.assertIn('C1',self.request('/api/project')[2]['components'])
        report_id=self.request('/api/history')[2][0]['id']
        status,headers,body=self.request(f'/reports/{report_id}/report.html')
        self.assertEqual(status,200)
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertIn(b'<!doctype html>',body.lower())
        self.state.close();self.assertFalse(path.exists())

    def test_unsafe_upload_and_download_paths(self):
        for name in ['../bad.zip','%2Ftmp%2Fbad.zip','file.txt','bad%00.zip','bad%0A.zip']:
            self.assertEqual(self.request('/api/upload',raw=b'',headers={'X-Filename':name})[0],400)
        self.assertEqual(self.request('/api/upload',raw=b'',headers={'Content-Length':str(101*1024*1024)})[0],413)
        for path in ['/reports/../webapp.py','/reports/%2E%2E/report.json','/reports/example/%2E%2E%2Fwebapp.py','/reports/example/not-allowed.txt']:
            self.assertEqual(self.request(path)[0],404)
        self.state.reports.mkdir(exist_ok=True)
        external=self.root/'external';external.mkdir();(external/'report.json').write_text('{}')
        (self.state.reports/'linked').symlink_to(external,target_is_directory=True)
        self.assertEqual(self.request('/reports/linked/report.json')[0],404)


class InterpretRouteTests(unittest.TestCase):
    """Socket-free: exercise the same route function the HTTP handler calls."""

    def setUp(self):
        from pcb_checker.breadboard_to_PCB import livescan as store
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = AppState('/example/default', self.root / 'reports')
        self.pid = 'ls-routecheck'
        store.save_project(self.state.reports, self.pid, {
            'version': 1, 'id': self.pid, 'name': 'r', 'created': '', 'updated': '',
            'breadboard': {'size': 'full', 'railGaps': {}},
            'intent': {'text': '', 'fields': {}, 'updated': None},
            'components': [], 'wires': [], 'images': [], 'questions': [],
            'confirmations': {}, 'counters': {}})
        store.save_image(self.state.reports, self.pid, 'view.jpg', b'\xff\xd8fakejpeg')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_missing_image_key_and_status_mapping(self):
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker.webapp import interpret_scan_route, AppError
        with self.assertRaises(AppError) as missing:
            interpret_scan_route(self.state, self.pid, {'image': 'missing.jpg'})
        self.assertEqual(missing.exception.status, 404)
        self.state.meta_key = ''
        with self.assertRaises(AppError) as nokey:
            interpret_scan_route(self.state, self.pid, {'image': 'view.jpg'})
        self.assertEqual(nokey.exception.status, 400)
        self.assertIn('API key', str(nokey.exception))
        self.state.meta_key = 'test-meta-key'
        canned = {'observations': {'board': {}, 'components': [], 'wires': [],
                                   'questions': [], 'next_view': ''},
                  'usage': {'total_tokens': 9}, 'response_id': 'r1'}
        with patch('pcb_checker.breadboard_to_PCB.livescan_vision.interpret', return_value=canned) as vision:
            body = interpret_scan_route(
                self.state, self.pid, {'image': 'view.jpg', 'view_hint': 'h',
                                       'known': 'k', 'manual': True})
            self.assertEqual(body['usage'], {'total_tokens': 9})
            self.assertNotIn('test-meta-key', json.dumps(body))
            self.assertEqual(vision.call_args.kwargs['key'], 'test-meta-key')
            self.assertTrue(vision.call_args.kwargs['manual'])
            self.assertEqual(vision.call_args.kwargs['project_id'], self.pid)
            self.assertEqual(vision.call_args.kwargs['suffix'], '.jpg')
        with patch('pcb_checker.breadboard_to_PCB.livescan_vision.interpret',
                   side_effect=AIError('cooling down', diagnostics={'error_kind': 'cooldown'})):
            with self.assertRaises(AppError) as cool:
                interpret_scan_route(self.state, self.pid, {'image': 'view.jpg'})
            self.assertEqual(cool.exception.status, 429)
        with patch('pcb_checker.breadboard_to_PCB.livescan_vision.interpret',
                   side_effect=AIError('broken')):
            with self.assertRaises(AppError) as broken:
                interpret_scan_route(self.state, self.pid, {'image': 'view.jpg'})
            self.assertEqual(broken.exception.status, 502)


class FirmwareSeedTests(unittest.TestCase):
    def test_reopen_seeds_latest_same_report_answers(self):
        jobs = {
            'old': {'id': 'old', 'report_id': 'r1', 'status': 'validated',
                    'answers': [{'need': 'Q1', 'answer': 'stale'}]},
            'new': {'id': 'new', 'report_id': 'r1', 'status': 'validated',
                    'answers': [{'need': 'Q1', 'answer': 'fresh'},
                                {'need': '', 'answer': 'junk'},
                                {'need': 'Q2'}]},
            'other': {'id': 'other', 'report_id': 'r2', 'status': 'validated',
                      'answers': [{'need': 'Q9', 'answer': 'elsewhere'}]},
        }
        seeded = AppState._seeded_firmware_answers(jobs, 'r1', 'next')
        self.assertEqual(seeded, [{'need': 'Q1', 'answer': 'fresh'}])

    def test_reopen_unions_answers_scattered_across_builds(self):
        jobs = {
            'a': {'id': 'a', 'report_id': 'r1', 'status': 'validated',
                  'answers': [{'need': 'Q1', 'answer': 'one'}]},
            'b': {'id': 'b', 'report_id': 'r1', 'status': 'validated',
                  'answers': [{'need': 'Q2', 'answer': 'two'}]},
        }
        seeded = AppState._seeded_firmware_answers(jobs, 'r1', 'next')
        self.assertEqual(seeded, [{'need': 'Q1', 'answer': 'one'},
                                  {'need': 'Q2', 'answer': 'two'}])

    def test_no_seed_without_validated_same_report_build(self):
        jobs = {'a': {'id': 'a', 'report_id': 'r1', 'status': 'preparing',
                      'answers': [{'need': 'Q1', 'answer': 'x'}]}}
        self.assertEqual(AppState._seeded_firmware_answers(jobs, 'r1', 'next'), [])
        self.assertEqual(AppState._seeded_firmware_answers({}, 'r1', 'next'), [])


class ServerUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'project'
        self.source.mkdir()
        self.state = AppState(self.source, self.root/'reports')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def test_config_carries_stable_server_id(self):
        first = self.state.config()['server_id']
        self.assertTrue(first)
        self.assertEqual(self.state.config()['server_id'], first)

    def test_restart_issues_new_server_id(self):
        first = self.state.config()['server_id']
        restarted = AppState(self.source, self.root/'reports')
        try:
            second = restarted.config()['server_id']
            self.assertTrue(second)
            self.assertNotEqual(second, first)
        finally:
            restarted.close()

    def test_index_html_scripts_are_cache_busted(self):
        from pcb_checker import webapp
        raw = (Path(webapp.__file__).resolve().parent/'web'/'index.html').read_bytes()
        self.assertIn(b'src="/app.js"', raw)
        out = _cache_busted_html(raw).decode('utf-8')
        self.assertIn('app.js?v=' + ASSET_TAG, out)
        self.assertNotIn('src="/app.js"', out)
        self.assertNotIn('src="/workflow.js"', out)


class LauncherTests(unittest.TestCase):
    def test_web_skips_prompt_and_passes_defaults(self):
        # Hermetic reports dir: the launcher reopens the last saved project,
        # so this "no saved state" case needs a clean database, not the
        # checkout's live one (which may hold a linked demo from real runs).
        clean = tempfile.TemporaryDirectory()
        self.addCleanup(clean.cleanup)
        with patch('sys.argv',['main.py','--web','--no-browser','--no-ai','--output-dir',clean.name]), patch.dict('os.environ',{},clear=True), patch('pcb_checker.webapp.serve',return_value=0) as serve, patch('pcb_checker.__main__.prompt_project_folder') as prompt:
            self.assertEqual(main(),0)
        prompt.assert_not_called()
        self.assertFalse(serve.call_args.kwargs['open_browser'])
        self.assertFalse(serve.call_args.kwargs['ai'])
        # No built-in board path: with no archive, env project or saved
        # project, the browser opens empty instead of reusing demo data.
        self.assertEqual(serve.call_args.args[0],'')


class DemoProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.demo = self.root / 'Demo'
        self.demo.mkdir()
        (self.demo / 'demo.kicad_pro').write_text('{}')
        (self.demo / 'demo.kicad_pcb').write_text(board(footprint()))
        (self.demo / 'demo.kicad_sch').write_text(schematic(symbol()))
        self.state = AppState(self.root / 'src', self.root / 'reports')
        self.state.demo_projects = lambda: {'demos': [{'name': 'Demo',
                                                       'directory': str(self.demo),
                                                       'files': ['demo.kicad_pro', 'demo.kicad_pcb',
                                                                 'demo.kicad_sch']}]}

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def seed_review(self, archive='Demo', report_id='Demo-1'):
        folder = self.root / 'reports' / report_id
        folder.mkdir(parents=True)
        (folder / 'report.json').write_text(json.dumps({'archive': archive}))
        self.state.store.preference('input:' + report_id,
                                    {'path': str(self.demo.resolve())})
        keys = self.state._demo_context_keys(self.demo, {report_id})
        self.state.store.save_context(next(iter(keys)),
                                      {'revision': 0, 'question_answers':
                                       {'q1': {'question': 'Q?', 'answer': 'A'}}}, 0)
        return keys

    def test_reset_removes_demo_state_and_keeps_board_files(self):
        original = {item.name: (self.demo / item.name).read_bytes()
                    for item in self.demo.iterdir() if item.is_file()}
        keys = self.seed_review()
        self.state.store.preference('linked_kicad_files', {'path': str(self.demo)})
        result = self.state.reset_demo_project('Demo')
        self.assertEqual(result['reset'], 'Demo')
        self.assertEqual(result['removed_reports'], ['Demo-1'])
        self.assertFalse((self.root / 'reports' / 'Demo-1').exists())
        self.assertIsNone(self.state.store.design_context(next(iter(keys))))
        # Reset re-links a fresh pristine working copy so the demo is present.
        workdir = (self.root / 'reports' / '.demo-work' / 'Demo').resolve()
        self.assertEqual(result['linked'], str(workdir))
        self.assertEqual(self.state.store.preference('linked_kicad_files')['path'], str(workdir))
        for name, payload in original.items():
            self.assertEqual((workdir / name).read_bytes(), payload)
            # Bundled originals are byte-identical and untouched.
            self.assertEqual((self.demo / name).read_bytes(), payload)

    def test_open_demo_uses_working_copy_not_originals(self):
        original = {item.name: (self.demo / item.name).read_bytes()
                    for item in self.demo.iterdir() if item.is_file()}
        linked = self.state.open_demo_project('Demo')
        workdir = (self.root / 'reports' / '.demo-work' / 'Demo').resolve()
        self.assertEqual(linked['path'], str(workdir))
        for name, payload in original.items():
            self.assertEqual((workdir / name).read_bytes(), payload)
            self.assertEqual((self.demo / name).read_bytes(), payload)

    def test_open_demo_reuses_existing_copy(self):
        self.state.open_demo_project('Demo')
        workdir = (self.root / 'reports' / '.demo-work' / 'Demo').resolve()
        (workdir / 'demo.kicad_pcb').write_bytes(b'modified by judge')
        linked = self.state.open_demo_project('Demo')
        self.assertEqual(linked['path'], str(workdir))
        self.assertEqual((workdir / 'demo.kicad_pcb').read_bytes(), b'modified by judge')

    def test_reset_restores_pristine_copy(self):
        original = {item.name: (self.demo / item.name).read_bytes()
                    for item in self.demo.iterdir() if item.is_file()}
        self.state.open_demo_project('Demo')
        workdir = (self.root / 'reports' / '.demo-work' / 'Demo').resolve()
        (workdir / 'demo.kicad_pcb').write_bytes(b'dirtied')
        self.seed_review()
        result = self.state.reset_demo_project('Demo')
        self.assertEqual(result['linked'], str(workdir))
        for name, payload in original.items():
            self.assertEqual((workdir / name).read_bytes(), payload)
            self.assertEqual((self.demo / name).read_bytes(), payload)

    def test_reset_preserves_unrelated_link(self):
        other = self.root / 'other'
        other.mkdir()
        self.state.store.preference('linked_kicad_files', {'path': str(other)})
        result = self.state.reset_demo_project('Demo')
        self.assertIsNone(result['linked'])
        self.assertEqual(self.state.store.preference('linked_kicad_files')['path'], str(other))

    def test_open_demo_unloads_previous_report_view(self):
        # Switching demos must clear the previously loaded report so the old
        # board's results can never linger under the newly linked files.
        self.state.open_demo_project('Demo')
        self.state.project, self.state.report_id = object(), 'Demo-1'
        before = self.state.revision
        self.state.open_demo_project('Demo')
        self.assertIsNone(self.state.project)
        self.assertIsNone(self.state.report_id)
        self.assertGreater(self.state.revision, before)
        workdir = (self.root / 'reports' / '.demo-work' / 'Demo').resolve()
        self.assertTrue((workdir / 'demo.kicad_pcb').is_file())

    def test_reset_keeps_other_projects_and_rejects_unknown_names(self):
        self.seed_review()
        other = self.root / 'reports' / 'Other-9'
        other.mkdir(parents=True)
        (other / 'report.json').write_text(json.dumps({'archive': 'Other'}))
        result = self.state.reset_demo_project('Demo')
        self.assertTrue(other.is_dir())
        self.assertEqual(result['removed_reports'], ['Demo-1'])
        with self.assertRaises(AppError):
            self.state.reset_demo_project('Not a demo')
        with self.assertRaises(AppError):
            self.state.reset_demo_project('../escape')

    def test_open_demo_in_kicad_prefers_working_copy(self):
        with patch('subprocess.Popen') as popen:
            result = self.state.open_demo_in_kicad('Demo')
        self.assertEqual(result['opened'], 'demo.kicad_pro')
        target = popen.call_args.args[0][1]
        self.assertTrue(target.endswith('demo.kicad_pro'))
        self.assertIn('.demo-work', target)

    def test_open_demo_rejects_unknown_names(self):
        with self.assertRaises(AppError):
            self.state.open_demo_in_kicad('Not a demo')


class RethinkEconomyTests(unittest.TestCase):
    """Context-only rethinks must be free, local, and reuse verified results."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root/'project'
        self.source.mkdir()
        (self.source/'example.kicad_sch').write_text(schematic(symbol()))
        (self.source/'example.kicad_pcb').write_text(board(footprint()))
        self.state = AppState(self.source, self.root/'reports')

    def tearDown(self):
        self.state.close()
        self.temp.cleanup()

    def finish(self):
        self.state.worker.join(timeout=45)
        self.assertFalse(self.state.worker.is_alive(), self.state.snapshot().get('job'))
        return self.state.snapshot()['job']

    def analyzed(self):
        self.state.start('analysis', {'path': str(self.source), 'native': False, 'datasheets': False})
        self.assertEqual(self.finish()['status'], 'completed')
        return self.state.report_id

    def rethink(self, **extra):
        revision = self.state.project_data()['workspace']['design_context']['revision']
        self.state.start('rethink', {'report_id': self.state.report_id, 'context_revision': revision,
                                     'context_intake': True, 'ai': False, 'datasheets': False, **extra})
        return self.finish()

    def test_context_only_rethink_skips_copper_assembly_and_ai(self):
        first = self.analyzed()
        with patch('pcb_checker.signal_paths.analyze_signal_paths') as signals, \
             patch('pcb_checker.routing_analysis.analyze_routing') as routing, \
             patch('pcb_checker.assembly_export.generate_assembly') as assembly, \
             patch('pcb_checker.webapp.run_ai_review') as review:
            job = self.rethink(description='Battery-powered sensor node.')
        self.assertEqual(job['status'], 'completed')
        signals.assert_not_called()
        routing.assert_not_called()
        assembly.assert_not_called()
        review.assert_not_called()
        self.assertNotEqual(self.state.report_id, first)
        self.assertTrue(self.state.report_path(first).is_file(), 'Previous report preserved')
        self.assertEqual(self.state.project.design_context.get('description'),
                         'Battery-powered sensor node.')

    def test_changed_source_forces_recomputation(self):
        self.analyzed()
        with open(self.source/'example.kicad_pcb', 'a') as handle:
            handle.write('\n')
        with patch('pcb_checker.signal_paths.analyze_signal_paths') as signals, \
             patch('pcb_checker.routing_analysis.analyze_routing') as routing, \
             patch('pcb_checker.webapp.run_ai_review') as review:
            job = self.rethink(description='Same project, touched file.')
        self.assertEqual(job['status'], 'completed')
        signals.assert_called_once()
        routing.assert_called_once()
        review.assert_not_called()

    def test_busy_state_rejects_duplicate_work(self):
        self.analyzed()
        self.state.job = {'id': 'stuck', 'status': 'running', 'message': '…', 'events': []}
        with self.assertRaises(AppError):
            self.state.start('rethink', {'report_id': self.state.report_id})
        self.state.job = None

    def test_traceback_redaction_covers_all_key_slots(self):
        from pcb_checker.ai.reviewer import redact_error
        trace = ('Traceback (most recent call last):\n  File "webapp.py", line 1, in get\n'
                 "key=local-test-secret meta=LLAMA-KEY-2 openai=sk-test-3")
        cleaned = redact_error(trace, ['local-test-secret', 'LLAMA-KEY-2', 'sk-test-3'])
        for secret in ('local-test-secret', 'LLAMA-KEY-2', 'sk-test-3'):
            self.assertNotIn(secret, cleaned)
        self.assertIn('Traceback', cleaned)


if __name__ == '__main__':
    unittest.main()
