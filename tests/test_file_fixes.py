import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import file_fixes as fixes
from pcb_checker.analysis import analyze_project
from pcb_checker.models import Component, Issue, Pin, Project
from pcb_checker.parsers.sexpr import parse
from pcb_checker.webapp import AppState, AppError, prior_design_answers
from test_components import schematic, symbol, board, footprint


PCB = '''(kicad_pcb (version 20241229)
 (layers (0 "F.Cu" signal) (31 "B.Cu" signal)) (net 1 "GND")
 (footprint "Test" (at 10 20) (layer "F.Cu") (uuid "fp") (property "Reference" "U1")
  (pad "1" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND") (uuid "pad")))
 (segment (start 11 20) (end 12 20) (width .2) (layer "F.Cu") (net 1) (uuid "track")))'''


class FileFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'project';self.source.mkdir()
        (self.source/'test.kicad_sch').write_text(schematic(symbol(value='10nF')))
        (self.source/'test.kicad_pcb').write_text(board(footprint(value='100nF')))
        self.project=analyze_project(self.source)
        self.group=next(g for g in self.project.review['groups'] if any(
            self.project.issues[int(i[1:])-1].code=='VALUE_MISMATCH' for i in g['issue_ids']))
        self.folder=self.root/'prepared'

    def test_requested_value_change_parses_ref_and_value(self):
        from pcb_checker.file_fixes import parse_value_request
        refs = ['R12', 'R13', 'C105']
        self.assertEqual(parse_value_request('change R12 to 680', refs), ('R12', '680'))
        self.assertEqual(parse_value_request('Change R12 from 10 kΩ to 680 Ω.', refs), ('R12', '680'))
        self.assertEqual(parse_value_request('set C105 to 100nF', refs), ('C105', '100nF'))
        self.assertEqual(parse_value_request('R13 = 4k7', refs), ('R13', '4k7'))
        self.assertIsNone(parse_value_request('make the LEDs brighter', refs))
        self.assertIsNone(parse_value_request('change R99 to 680', refs))
        self.assertIsNone(parse_value_request('change R12 to a bigger value', refs))

    def test_requested_value_repair_skips_geometry_planner(self):
        from pcb_checker import board_edits
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'project'
            source.mkdir()
            (source / 'demo.kicad_sch').write_text(schematic(symbol(ref='R12', value='10k')))
            (source / 'demo.kicad_pcb').write_text(board(footprint(ref='R12', value='10k')))
            project = analyze_project(source)
            group = {'id': 'RG', 'rule': 'led_series_resistance', 'components': ['R12']}
            with patch.object(board_edits, 'propose', side_effect=AssertionError('geometry planner must not run')):
                plan = board_edits.prepare(project, group, source, root / 'fix',
                                            request='Change R12 from 10 kΩ to 680 Ω.',
                                            key='k', model='m')
            self.assertEqual(plan['status'], 'ready')
            self.assertIn('R12', plan['summary'])
            self.assertIn('10k', plan['summary'])
            self.assertIn('680', plan['summary'])
            applied = fixes.apply(plan, root / 'fix')
            self.assertEqual(applied['status'], 'applied')
            self.assertIn('680', (source / 'demo.kicad_sch').read_text())
            self.assertIn('680', (source / 'demo.kicad_pcb').read_text())
            undone = fixes.apply(plan, root / 'fix', undo=True)
            self.assertEqual(undone['status'], 'undone')
            self.assertIn('10k', (source / 'demo.kicad_sch').read_text())
            self.assertIn('10k', (source / 'demo.kicad_pcb').read_text())

    def test_requested_value_change_parses_ref_lists(self):
        from pcb_checker.file_fixes import parse_value_requests
        refs = ['R12', 'R13', 'R14']
        self.assertEqual(parse_value_requests('Change R12, R13, R14 from 10k to 680.', refs),
                         [('R12', '680'), ('R13', '680'), ('R14', '680')])
        self.assertEqual(parse_value_requests('Set R12, R13 and R14 to 680.', refs),
                         [('R12', '680'), ('R13', '680'), ('R14', '680')])
        self.assertEqual(parse_value_requests('Change R12 from 10 kΩ to 680 Ω.', refs), [('R12', '680')])
        self.assertEqual(parse_value_requests('R12 = 680', refs), [('R12', '680')])
        self.assertIsNone(parse_value_requests('Change R12 to 680 and R13 to 4k7.', refs))
        self.assertIsNone(parse_value_requests('Change R12, R99 to 680.', refs))
        self.assertIsNone(parse_value_requests('make the LEDs brighter', refs))

    def test_requested_multi_value_repair_skips_geometry_planner(self):
        from pcb_checker import board_edits
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'project'
            source.mkdir()
            (source / 'demo.kicad_sch').write_text(schematic(
                symbol(ref='R12', value='10k'), symbol(ref='R13', value='10k'), symbol(ref='R14', value='10k')))
            (source / 'demo.kicad_pcb').write_text(board(
                footprint(ref='R12', value='10k'), footprint(ref='R13', value='10k'), footprint(ref='R14', value='10k')))
            project = analyze_project(source)
            group = {'id': 'RG', 'rule': 'led_series_resistance', 'components': ['R12', 'R13', 'R14']}
            with patch.object(board_edits, 'propose', side_effect=AssertionError('geometry planner must not run')):
                plan = board_edits.prepare(project, group, source, root / 'fix',
                                            request='Change R12, R13, R14 from 10k to 680 ohms.',
                                            key='k', model='m')
            self.assertEqual(plan['status'], 'ready')
            for ref in ('R12', 'R13', 'R14'):
                self.assertIn(ref, plan['summary'])
            sch = (source / 'demo.kicad_sch').read_text()
            self.assertNotIn('680', sch)
            applied = fixes.apply(plan, root / 'fix')
            self.assertEqual(applied['status'], 'applied')
            self.assertEqual((source / 'demo.kicad_sch').read_text().count('"680"'), 3)
            self.assertEqual((source / 'demo.kicad_pcb').read_text().count('"680"'), 3)
            undone = fixes.apply(plan, root / 'fix', undo=True)
            self.assertEqual(undone['status'], 'undone')
            self.assertIn('10k', (source / 'demo.kicad_sch').read_text())
            self.assertIn('10k', (source / 'demo.kicad_pcb').read_text())

    def test_multi_value_request_fails_closed_on_unmatched_ref(self):
        from pcb_checker import board_edits
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'project'
            source.mkdir()
            (source / 'demo.kicad_sch').write_text(schematic(
                symbol(ref='R12', value='10k'), symbol(ref='R13', value='10k')))
            (source / 'demo.kicad_pcb').write_text(board(footprint(ref='R12', value='10k')))
            project = analyze_project(source)
            group = {'id': 'RG', 'rule': 'led_series_resistance', 'components': ['R12', 'R13']}
            with patch.object(board_edits, 'propose', side_effect=AssertionError('geometry planner must not run')):
                with self.assertRaises(Exception):
                    board_edits.prepare(project, group, source, root / 'fix',
                                        request='Change R12, R13 from 10k to 680 ohms.',
                                        key='k', model='m')
            self.assertNotIn('680', (source / 'demo.kicad_sch').read_text())
            self.assertNotIn('680', (source / 'demo.kicad_pcb').read_text())
            self.assertFalse((root / 'fix').exists())

    def test_repair_answers_carry_forward_only_for_same_finding_and_files(self):
        records={'old':{'id':'old','kind':'design_plan','status':'blocked','report_id':'review',
                        'group_id':'R2','hashes':{'board':'a'},
                        'design_answers':{'enclosure':'unknown','battery':'find a suitable cell'}},
                 'other-report':{'id':'other-report','kind':'design_plan','report_id':'another',
                        'group_id':'R2','hashes':{'board':'a'},'design_answers':{'enclosure':'metal'}},
                 'stale-files':{'id':'stale-files','kind':'design_plan','report_id':'review',
                        'group_id':'R2','hashes':{'board':'b'},'design_answers':{'enclosure':'metal'}}}
        saved=self.root/'.file-fixes'/'old';saved.mkdir(parents=True)
        (saved/'plan.json').write_text('{}')
        self.assertEqual(prior_design_answers(records,'review','R2',{'board':'a'},self.root),
                         {'enclosure':'unknown','battery':'find a suitable cell'})
        self.assertEqual(prior_design_answers(records,'review','R2',{'board':'c'},self.root),{})

    def test_repair_investigation_inherits_answers_before_worker_starts(self):
        self.project.issues=[Issue('warning','RF_FEED_NO_TERMINAL','No antenna endpoint',
                                   details={'signal_path_id':'rf-U13'})]
        group={'id':'R1','issue_ids':['F0001'],'components':['U13'],'signal_path_id':'rf-U13'}
        self.project.review={'groups':[group]}
        self.project.signal_paths={'paths':[{'id':'rf-U13','kind':'rf',
            'nets':[{'net':'ANT','copper_mm':3.2,'vias':0},
                    {'net':'RF_FEED','copper_mm':.93,'vias':0}]}]}
        state=AppState(self.source,self.root/'reports',key='test-key',model='test-model')
        self.addCleanup(state.close)
        state.project=self.project;state.report_id='review'
        state.store.preference('input:review',{'path':str(self.source)})
        old=state.reports/'.file-fixes'/'old';old.mkdir(parents=True)
        (old/'plan.json').write_text('{}')
        state.file_fixes['old']={'id':'old','kind':'design_plan','status':'blocked','report_id':'review','group_id':'R1',
            'hashes':self.project.provenance['source_hashes'],
            'design_answers':{'external_enclosure_metal':'No nearby metal; research a suitable battery.'}}
        design={'title':'RF repair investigation','questions':[]}
        with patch('pcb_checker.repair_design.investigate',return_value=design) as investigate:
            job=state.prepare_file_fix({'report_id':'review','group_id':'R1','investigate_design':True,
                'edit_request':'Investigate the RF path and the best battery position.',
                'design_answers':{'battery_capacity':'Not decided; recommend a suitable option.'}})
            for _ in range(200):
                current=state.file_fix(job['id'])
                if current['status']!='preparing':break
                time.sleep(.01)
        self.assertEqual(current['status'],'awaiting_design_approval',current)
        self.assertEqual(investigate.call_args.args[3],{
            'external_enclosure_metal':'No nearby metal; research a suitable battery.',
            'battery_capacity':'Not decided; recommend a suitable option.'})
        self.assertEqual(current['design_version'],9)

    def prepare(self):
        return fixes.prepare(self.project,self.group,self.source,self.folder)

    def test_prepare_reuses_record_for_identical_request(self):
        state=AppState(self.source,self.root/'reports',key='test-key',model='test-model')
        self.addCleanup(state.close)
        state.project=self.project;state.report_id='review'
        state.store.preference('input:review',{'path':str(self.source)})
        first=state.prepare_file_fix({'report_id':'review','group_id':self.group['id']})
        state.fix_worker.join(timeout=120)
        current=state.file_fix(first['id'])
        self.assertNotEqual(current['status'],'preparing',current)
        # A retry of the same request (e.g. after a client timeout) reuses
        # the finished preparation instead of forking duplicate work.
        again=state.prepare_file_fix({'report_id':'review','group_id':self.group['id']})
        self.assertEqual(again['id'],first['id'])

    def test_preview_apply_undo_changes_only_value_and_keeps_backup(self):
        original=(self.source/'test.kicad_pcb').read_bytes();plan=self.prepare()
        self.assertEqual((self.source/'test.kicad_pcb').read_bytes(),original)
        self.assertEqual((self.folder/'original.kicad_pcb').read_bytes(),original)
        result=fixes.apply(plan,self.folder)
        self.assertEqual(result['status'],'applied')
        self.assertEqual((self.source/'test.kicad_pcb').read_bytes(),original.replace(b'100nF',b'10nF'))
        updated=analyze_project(self.source)
        self.assertFalse(any(i.code=='VALUE_MISMATCH' for i in updated.issues))
        with self.assertRaises(fixes.FixError):fixes.apply(plan,self.folder)
        fixes.apply(plan,self.folder,undo=True)
        self.assertEqual((self.source/'test.kicad_pcb').read_bytes(),original)

    def test_changed_source_prevents_prepare_and_apply(self):
        plan=self.prepare();path=self.source/'test.kicad_sch';path.write_text(path.read_text()+'\n')
        with self.assertRaises(fixes.FixError):fixes.apply(plan,self.folder)
        with self.assertRaises(fixes.FixError):fixes.prepare(self.project,self.group,self.source,self.root/'other')

    def test_undo_refuses_to_erase_new_user_edits(self):
        plan=self.prepare();fixes.apply(plan,self.folder)
        path=self.source/'test.kicad_pcb';path.write_text(path.read_text()+'\n; user edit\n')
        with self.assertRaises(fixes.FixError):fixes.apply(plan,self.folder,undo=True)
        self.assertIn('user edit',path.read_text())

    def test_missing_or_tampered_backup_blocks_apply(self):
        plan=self.prepare();(self.folder/'original.kicad_pcb').write_bytes(b'tampered')
        with self.assertRaises(fixes.FixError):fixes.apply(plan,self.folder)

    def test_path_traversal_and_symlinks_are_rejected(self):
        with self.assertRaises(fixes.FixError):fixes.source_file(self.source,'../outside')
        link=self.source/'link';link.symlink_to(self.source/'test.kicad_pcb')
        with self.assertRaises(fixes.FixError):fixes.source_file(self.source,'link')

    def test_source_status_marks_changed_and_missing_linked_files(self):
        path=self.source/'test.kicad_pcb'
        hashes={'test.kicad_pcb':fixes.digest(path.read_bytes())}
        self.assertEqual(fixes.source_status(self.source,hashes)['status'],'current')
        path.write_text(path.read_text()+'\n')
        self.assertEqual(fixes.source_status(self.source,hashes)['changed'],['test.kicad_pcb'])
        path.unlink()
        status=fixes.source_status(self.source,hashes)
        self.assertEqual(status['status'],'changed')
        self.assertEqual(status['missing'],['test.kicad_pcb'])

    def test_locked_board_blocks_apply(self):
        plan=self.prepare();(self.source/'~test.kicad_pcb.lck').write_text('open')
        with self.assertRaises(fixes.FixError):fixes.apply(plan,self.folder)

    def test_unsupported_advice_cannot_become_an_edit(self):
        self.project.issues=[Issue('warning','KICAD_DRC_CLEARANCE','move copper')]
        with self.assertRaises(fixes.FixError):
            fixes.prepare(self.project,{'issue_ids':['F0001']},self.source,self.folder)

    def test_capability_rejects_unsupported_before_starting_job(self):
        self.assertEqual(fixes.availability(self.project,self.group)['mode'],'automatic')
        self.project.issues=[Issue('warning','RF_FEED_NO_TERMINAL','No antenna identified')]
        group={'id':'R1','issue_ids':['F0001']};self.project.review={'groups':[group]}
        self.assertEqual(fixes.availability(self.project,group)['mode'],'investigate')
        self.assertIn('does not prove',fixes.availability(self.project,group)['message'])
        self.assertEqual(fixes.availability(self.project,{'issue_ids':[]})['mode'],'investigate')
        state=AppState(self.source,self.root/'reports');self.addCleanup(state.close)
        state.project=self.project;state.report_id='test-review'
        with self.assertRaisesRegex(AppError,'antenna endpoint'):
            state.prepare_file_fix({'report_id':'test-review','group_id':'R1'})
        self.assertEqual(state.file_fixes,{})
        self.assertFalse(state.busy())

    def test_server_rejects_edit_requests_for_no_change_or_reference_only_findings(self):
        state=AppState(self.source,self.root/'reports',key='test-key',model='test-model')
        self.addCleanup(state.close)
        state.project=self.project;state.report_id='test-review'
        group_id=self.group['id']
        for action in (
            {'group_id':group_id,'disposition':'no_change'},
            {'group_id':group_id,'disposition':'investigate','reference_only':True},
        ):
            state.project.action_plan={'actions':[action]}
            with self.subTest(action=action), self.assertRaisesRegex(AppError,'does not support a file change'):
                state.prepare_file_fix({'report_id':'test-review','group_id':group_id,
                    'edit_request':'Change the design to address this finding.'})
        self.assertEqual(state.file_fixes,{})
        self.assertFalse(state.busy())

    def test_rf_assessment_uses_only_selected_path_and_cannot_enable_an_edit(self):
        self.project.issues=[Issue('warning','RF_FEED_NO_TERMINAL','Unknown endpoint',
                                 details={'signal_path_id':'rf-U9'})]
        group={'id':'R1','issue_ids':['F0001']}
        self.project.signal_paths={'paths':[
            {'id':'rf-U8','kind':'rf','nets':[{'net':'WRONG_PATH','copper_mm':999,'vias':5}]},
            {'id':'rf-U9','kind':'rf','nets':[{'net':'RF_OUT','copper_mm':.93,'vias':0}]}]}
        option=fixes.availability(self.project,group)
        self.assertEqual(option['mode'],'investigate')
        self.assertEqual(option['assessment']['status'],'investigate')
        text=' '.join(option['assessment']['observations'])
        self.assertIn('RF_OUT',text)
        self.assertIn('0.93 mm',text)
        self.assertNotIn('WRONG_PATH',text)
        with self.assertRaises(fixes.FixError):self.prepare()
        self.project.signal_paths={'paths':[]}
        self.assertIn('no matching RF path',fixes.availability(self.project,group)['assessment']['observations'][0])

    def test_quoted_value_preserves_structure(self):
        self.project.components['C1'].schematic[0].value='10nF "special" \\ part'
        plan=self.prepare();new=(self.folder/'replacement.kicad_pcb').read_text()
        parsed=parse(new,'kicad_pcb')
        self.assertEqual(parsed.first('footprint').all('property')[1].values[1],plan['after'])

    def ground(self):
        p=Project('test');p.components['U1']=Component('U1',schematic_pins=[Pin('1',net='GND')])
        issue=Issue('error','KICAD_DRC_UNCONNECTED_ITEMS','gap',items=[{'uuid':'pad'},{'uuid':'track'}])
        return p,issue

    def test_ground_candidate_uses_exact_objects_and_preserves_existing_text(self):
        p,i=self.ground();candidate,plan=fixes.connection_candidate(p,i,PCB.encode())
        self.assertEqual(plan['pad'],'1');self.assertEqual(plan['net'],'GND')
        self.assertEqual(plan['length_mm'],1)
        root=parse(candidate,'kicad_pcb');self.assertEqual(len(root.all('segment')),2)
        self.assertEqual(root.all('segment')[1].get('net'),'1')
        self.assertEqual(root.all('segment')[1].first('end').values,['10.000000','20.000000'])

    def test_ground_candidate_rejects_other_nets_missing_pins_and_locked_tracks(self):
        p,i=self.ground()
        for content in [PCB.replace('"GND"','"SDA"'),PCB.replace('(segment (start','(segment locked (start')]:
            with self.assertRaises(fixes.FixError):fixes.connection_candidate(p,i,content.encode())
        p.components['U1'].schematic_pins=[]
        with self.assertRaises(fixes.FixError):fixes.connection_candidate(p,i,PCB.encode())

    def test_ground_repair_closes_routing_open_on_disposable_copy(self):
        from pcb_checker import routing_analysis as ra
        p, i = self.ground()
        candidate, plan = fixes.connection_candidate(p, i, PCB.encode())
        self.assertEqual(plan['kind'], 'ground_connection')
        # Routing oracle: the same 1 mm gap with a pad on both copper ends.
        route_board = PCB.replace(
            ' (segment (start 11 20) (end 12 20) (width .2) (layer "F.Cu") (net 1) (uuid "track"))',
            ' (segment (start 11 20) (end 12 20) (width .2) (layer "F.Cu") (net 1) (uuid "track"))\n'
            ' (footprint "Test2" (at 12 20) (layer "F.Cu") (uuid "fp2") (property "Reference" "U2")\n'
            '  (pad "1" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND") (uuid "pad2")))')
        new_segment = [line for line in candidate.splitlines()
                       if line.strip().startswith('(segment') and '(end 10' in line]
        self.assertEqual(len(new_segment), 1)
        topos = {}
        for label, text in (('before', route_board),
                            ('after', route_board.replace('(footprint "Test2"',
                                                          new_segment[0] + '\n (footprint "Test2"'))):
            # Disposable round-trip: write out and re-read, never touching sources.
            copy = self.root / f'{label}.kicad_pcb'
            copy.write_text(text)
            copper = ra.extract_copper(parse(copy.read_text(), 'kicad_pcb'))
            items = copper['nets']['GND']
            graph = ra.build_graph(items, copper['layers'])
            topos[label] = ra.pin_topology(graph, items['pads'], p,
                                           copper.get('zones'), items['vias'])
        self.assertEqual(len(topos['before']['disconnected']), 1)
        self.assertEqual(topos['after']['disconnected'], [])
        self.assertEqual(len(topos['after']['pairs']), len(topos['before']['pairs']) + 1)
        # No-pour fixture: the join comes from the new segment alone, and the
        # board carries no zone fills at all.
        self.assertEqual(topos['after']['pour_joined'], [])
        self.assertNotIn('zone', PCB)

    def test_drc_must_remove_target_without_introducing_findings(self):
        base={'violations':[], 'unconnected_items':[{'type':'unconnected_items','severity':'error','items':[{'uuid':'pad'},{'uuid':'track'}]}]}
        clear={'violations':[], 'unconnected_items':[]}
        before=self.root/'before';after=self.root/'after';before.mkdir();after.mkdir()
        def check(first,second):
            with patch.object(fixes,'prepare_board',return_value={'status':'prepared'}), patch.object(fixes,'_run') as run:
                def result(binary,args,directory,**kw):
                    (directory/'fix-drc.json').write_text(json.dumps(first if directory==before else second))
                run.side_effect=result
                return fixes.validate_connection(before,after,'test.kicad_pcb','fake',['pad','track'])
        self.assertEqual(len(check(base,clear)),3)
        with self.assertRaises(fixes.FixError):check(base,base)
        with self.assertRaises(fixes.FixError):check(clear,clear)
        bad=copy.deepcopy(clear);bad['violations']=[{'type':'clearance','severity':'error','items':[{'uuid':'new'}]}]
        with self.assertRaises(fixes.FixError):check(base,bad)

    def test_state_requires_separate_confirmation_and_restores_undo_after_restart(self):
        state=AppState(self.source,self.root/'reports');self.addCleanup(state.close)
        state.project=self.project;state.report_id='test-review'
        state.store.preference('input:test-review',{'path':str(self.source)})
        job=state.prepare_file_fix({'report_id':'test-review','group_id':self.group['id']})
        for _ in range(100):
            current=state.file_fix(job['id'])
            if current['status']!='preparing':break
            time.sleep(.01)
        self.assertEqual(current['status'],'ready',current)
        request={'fix_id':job['id']}
        with self.assertRaises(AppError):state.apply_file_fix(request)
        state.apply_file_fix({**request,'closed_in_kicad':True})
        restarted=AppState(self.source,self.root/'reports');self.addCleanup(restarted.close)
        restarted.report_id='test-review';restarted.project=self.project
        self.assertEqual(restarted.file_fix(job['id'])['status'],'applied')
        self.assertEqual(restarted.apply_file_fix({**request,'closed_in_kicad':True},undo=True)['status'],'undone')

    def test_repair_stall_watchdog_restarts_after_each_progress_update(self):
        state=AppState(self.source,self.root/'watchdog-reports');self.addCleanup(state.close)
        state.project=self.project;state.report_id='watchdog-review'
        state.store.preference('input:watchdog-review',{'path':str(self.source)})
        timers=[]
        class FakeTimer:
            def __init__(self,interval,function):self.interval=interval;self.function=function;self.cancelled=False;timers.append(self)
            def start(self):pass
            def cancel(self):self.cancelled=True
        def prepare(project,group,source,folder,*,kicad_cli,progress):
            progress('Checking schematic')
            progress('Running board DRC')
            return {'status':'ready','summary':'Validated draft'}
        with patch('pcb_checker.webapp.threading.Timer',FakeTimer),patch('pcb_checker.file_fixes.prepare',side_effect=prepare):
            job=state.prepare_file_fix({'report_id':'watchdog-review','group_id':self.group['id']})
            for _ in range(200):
                current=state.file_fix(job['id'])
                if current['status']!='preparing':break
                time.sleep(.01)
        self.assertEqual(current['status'],'ready',current)
        self.assertEqual([timer.interval for timer in timers],[20*60]*3)
        self.assertTrue(timers[0].cancelled and timers[1].cancelled and timers[2].cancelled)
