import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import board_edits as edits, file_fixes as fixes
from pcb_checker.analysis import analyze_project
from pcb_checker.board_locations import board_locations
from pcb_checker.native import find_kicad
from pcb_checker.webapp import AppState, AppError
from test_components import schematic, symbol

RUNTIME=Path('/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3')
CREATE='''
import pcbnew,sys
b=pcbnew.BOARD()
n=pcbnew.NETINFO_ITEM(b,'SIGNAL');b.Add(n)
g=pcbnew.NETINFO_ITEM(b,'GND');b.Add(g)
for ref,x in [('R1',10),('R2',20)]:
 f=pcbnew.FootprintLoad('/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints/Resistor_SMD.pretty','R_0603_1608Metric')
 b.Add(f);f.SetReference(ref);f.SetValue('10k');f.SetFPID(pcbnew.LIB_ID('Resistor_SMD','R_0603_1608Metric'))
 f.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x),pcbnew.FromMM(10)))
 f.SetField('LCSC','C12345')
 if ref=='R2':f.SetAttributes(f.GetAttributes() | pcbnew.FP_DNP | pcbnew.FP_EXCLUDE_FROM_BOM)
 for p in f.Pads():p.SetNet(n if p.GetNumber()=='1' else g)
for a,z in [((0,0),(30,0)),((30,0),(30,30)),((30,30),(0,30)),((0,30),(0,0))]:
 s=pcbnew.PCB_SHAPE();s.SetShape(pcbnew.SHAPE_T_SEGMENT);s.SetLayer(pcbnew.Edge_Cuts)
 s.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(a[0]),pcbnew.FromMM(a[1])))
 s.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(z[0]),pcbnew.FromMM(z[1])));s.SetWidth(pcbnew.FromMM(.05));b.Add(s)
pcbnew.SaveBoard(sys.argv[1],b)
'''


class ValidationTests(unittest.TestCase):
    def test_repair_schema_uses_flat_required_fields_with_local_operation_limits(self):
        # The seven-branch anyOf/maxItems schema failed against the live API.
        schema=edits.SCHEMA['properties']['operations']
        self.assertNotIn('maxItems',schema)
        self.assertNotIn('anyOf',schema['items'])
        self.assertEqual(set(schema['items']['required']),set(edits.OP_SCHEMA['properties']))
        for kind,fields in edits.FIELDS.items():
            self.assertTrue(fields <= set(schema['items']['required']),kind)
        with self.assertRaises(fixes.FixError):
            edits.validate_operations([{}]*(edits.MAX_EDIT_OPERATIONS+1),None)

    def test_nearby_movable_parts_contribute_all_their_signal_nets(self):
        part=lambda x,y,pads:SimpleNamespace(on_board=True,x_mm=x,y_mm=y,pads=[SimpleNamespace(net=n) for n in pads])
        project=SimpleNamespace(components={
            'U13':SimpleNamespace(pcb=[part(0,0,['RF','SPI_SCK','GND'])]),
            'R1':SimpleNamespace(pcb=[part(5,0,['SPI_MOSI','+3V3'])]),
            'J1':SimpleNamespace(pcb=[part(20,0,['USB_D+'])]),
            'X1':SimpleNamespace(pcb=[part(None,None,['NC'])]),
        })
        nets,refs=edits.nearby_component_nets(project,[(0,0)],radius_mm=10)
        self.assertEqual(nets,{'RF','SPI_SCK','SPI_MOSI','+3V3'})
        self.assertEqual(refs,{'U13','R1'})

    def test_gemini_json_requests_do_not_trigger_false_full_minute_cooldowns(self):
        from pcb_checker.ai import reviewer
        original=list(reviewer._GEMINI_INPUT_WINDOW)
        try:
            reviewer._GEMINI_INPUT_WINDOW.clear()
            with patch.object(reviewer.time,'monotonic',return_value=1000.0):
                reviewer._pace_gemini_request({'input':'x'*300_000})
                reviewer._pace_gemini_request({'input':'x'*300_000})
            self.assertEqual(sum(count for _,count in reviewer._GEMINI_INPUT_WINDOW),200_000)
        finally:
            reviewer._GEMINI_INPUT_WINDOW[:]=original

    def test_drc_correction_keeps_sourced_antenna_and_replaces_old_feed_route(self):
        previous=[
            {'kind':'add_reference_antenna','reference':'ANT1','footprint':'RF_Antenna:Johanson',
             'x_mm':1,'y_mm':2,'rotation_deg':0,'net':'RF','ground_net':'','reason':'Sourced part'},
            {'kind':'add_track','net':'RF','layer':'F.Cu','start_x_mm':0,'start_y_mm':0,
             'end_x_mm':1,'end_y_mm':1,'width_mm':.14,'reason':'First route'},
            {'kind':'move_footprint','reference':'R7','x_mm':4,'y_mm':5,'rotation_deg':0,'reason':'Clearance'},
        ]
        correction=[
            {'kind':'delete_track','uuid':'12345678-1234-1234-1234-123456789abc','reason':'Remove short'},
            {'kind':'add_track','net':'RF','layer':'F.Cu','start_x_mm':0,'start_y_mm':0,
             'end_x_mm':2,'end_y_mm':2,'width_mm':.14,'reason':'Corrected route'},
            {'kind':'add_track','net':'RF','layer':'F.Cu','start_x_mm':2,'start_y_mm':2,
             'end_x_mm':3,'end_y_mm':2,'width_mm':.14,'reason':'Route around collision'},
            {'kind':'move_footprint','reference':'ANT1','x_mm':2,'y_mm':3,'rotation_deg':90,'reason':'Clear the connector vias'},
        ]
        merged=edits.merge_operation_corrections(previous,correction)
        self.assertEqual([op['kind'] for op in merged],['add_reference_antenna','move_footprint','add_track','add_track','delete_track'])
        self.assertEqual(merged[0]['reference'],'ANT1')
        self.assertEqual((merged[0]['x_mm'],merged[0]['y_mm'],merged[0]['rotation_deg']),(2,3,90))
        self.assertEqual(merged[1]['reference'],'R7')
        self.assertEqual((merged[2]['end_x_mm'],merged[3]['start_x_mm']),(2,2))

    def test_failed_antenna_route_is_not_silently_kept_when_retry_omits_route(self):
        previous=[{'kind':'add_reference_antenna','reference':'ANT1','reason':'Sourced antenna'},
                  {'kind':'add_track','net':'RF','reason':'Known DRC-failing long feed'}]
        correction=[{'kind':'move_footprint','reference':'ANT1','reason':'Try another placement'}]
        merged=edits.merge_operation_corrections(previous,correction,drop_previous_tracks=True)
        self.assertEqual(merged,[previous[0],correction[0]])

    def test_draft_net_rename_replaces_failed_route_and_preserves_other_nets(self):
        previous=[{'kind':'add_reference_antenna','reference':'ANT1','net':'RF'},
                  {'kind':'add_track','net':'RF','reason':'Shorted draft'},
                  {'kind':'add_track','net':'SDA','reason':'Already repaired sensor route'}]
        correction=[{'kind':'add_track','net':'Net-(ANT1-A)','reason':'RF route correction'}]
        merged=edits.merge_operation_corrections(previous,correction)
        self.assertEqual([op.get('reason') for op in merged[1:]],['Already repaired sensor route','RF route correction'])
        self.assertEqual(merged[-1]['net'],'RF')
        self.assertEqual(correction[0]['net'],'Net-(ANT1-A)')

    def test_drc_correction_drops_only_deletions_of_temporary_tracks(self):
        previous=[{'kind':'add_track','net':'RF','reason':'Draft route'}]
        draft='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        original='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        operations=[{'kind':'delete_track','uuid':draft,'reason':'Remove draft short'},
                    {'kind':'delete_track','uuid':original,'reason':'Remove saved short'}]
        feedback={'exact_kicad_findings':[{'items':[{'uuid':draft}]}]}
        result=edits.discard_draft_track_deletions(operations,previous,feedback,{original})
        self.assertEqual([op['uuid'] for op in result],[original])

    def test_repair_feedback_includes_only_exact_new_drc_findings(self):
        report={'violations':[
            {'type':'clearance','severity':'error','description':'new antenna collision',
             'items':[{'uuid':'antenna-pad','description':'Pad 1 ANT1','pos':{'x':1,'y':2}},
                      {'uuid':'track','description':'Track SDA','pos':{'x':2,'y':3}}]},
            {'type':'clearance','severity':'error','description':'old nearby board violation',
             'items':[{'uuid':'antenna-pad','description':'Pad 1 ANT1','pos':{'x':1,'y':2}},
                      {'uuid':'old-track','description':'Old track','pos':{'x':3,'y':4}}]},
        ]}
        result=edits.exact_new_drc_findings(report,[('clearance','error',('antenna-pad','track'))])
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['description'],'new antenna collision')
        self.assertEqual(result[0]['items'][1]['position_mm'],{'x':2,'y':3})

    def test_drc_repair_instructions_use_current_failed_geometry(self):
        findings=[{'rule':'shorting_items','items':[
            {'description':'Pad 1 [RF] of ANT1','position_mm':{'x':-36.95,'y':-39}},
            {'description':'Track [J5]','position_mm':{'x':-36.31,'y':-39.0325}}]},
            {'rule':'courtyards_overlap','items':[{'description':'ANT1 courtyard / U13 courtyard'}]}]
        previous=[{'kind':'add_reference_antenna','reference':'ANT1','footprint':'RF_Antenna:chip',
                   'x_mm':-35.5,'y_mm':-39,'rotation_deg':0,'net':'RF','ground_net':''}]
        text=edits.drc_correction_instructions(findings,previous,2,5)
        self.assertIn('(-35.5, -39) mm',text)
        self.assertIn('(-36.31, -39.0325) mm',text)
        self.assertIn('courtyards_overlap',text)
        self.assertNotIn('33.8 mm diagonal',text)

    def test_failed_drc_locations_expand_the_next_geometry_context(self):
        feedback={'previous_operations':[{'kind':'add_reference_antenna','x_mm':-32.5,'y_mm':-1.5}],
            'exact_kicad_findings':[{'items':[{'position_mm':{'x':-36.5,'y':-39.95}}]}]}
        self.assertEqual(edits.repair_geometry_centers([(0,0)],feedback),[(0,0),(-32.5,-1.5),(-36.5,-39.95)])

    def test_antenna_repair_preserves_existing_matching_network_trace(self):
        track_uuid='5b248799-75ec-4cef-8eec-1280ac6ccca2'
        board=b'''(kicad_pcb (version 20241229) (generator "pcbnew")
          (net 19 "Net-(C33-Pad1)")
          (segment (start -36.69 -35.2) (end -35.765 -35.2) (width 0.138)
            (layer "F.Cu") (net 19) (uuid "5b248799-75ec-4cef-8eec-1280ac6ccca2")))'''
        operations=[{'kind':'delete_track','uuid':track_uuid,'reason':'Make room for antenna'},
                    {'kind':'add_track','net':'Net-(C33-Pad1)','reason':'New antenna feed'}]
        result=edits.preserve_saved_antenna_feed_tracks(operations,board,'Net-(C33-Pad1)')
        self.assertEqual(result,[operations[1]])

    def test_edit_prompt_keeps_signal_nets_and_nearby_copper_without_whole_board_routes(self):
        source=[
            {'kind':'footprint','point':[100,100],'reference':'U8'},
            {'kind':'track','point':[100,100],'points':[[100,100],[110,100]],'net':'RF_FEED'},
            {'kind':'track','point':[30,0],'points':[[30,0],[30,30]],'net':'OTHER'},
            {'kind':'track','point':[3,0],'points':[[3,0],[3,20]],'net':'GND'},
            {'kind':'via','point':[40,40],'net':'GND'},
            {'kind':'pad','point':[2,2],'net':'GND'},
        ]
        kept,scope=edits.focused_board_geometry(source,[(0,0)],{'RF_FEED','GND'},radius_mm=5)
        self.assertEqual(len(kept),4)
        self.assertEqual(scope['total_items'],6)
        self.assertEqual(scope['whole_board_footprints'],1)
        self.assertEqual(scope['complete_signal_nets'],1)
        self.assertEqual(scope['nearby_geometry'],2)
        self.assertNotIn({'kind':'via','point':[40,40],'net':'GND'},kept)

    def test_feed_can_be_rerouted_when_a_connected_component_moves(self):
        content=b'''(kicad_pcb (net 1 "RF")
          (footprint "L" (at 1 1) (property "Reference" "L1") (uuid "fp")
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "RF") (uuid "pad"))))'''
        operations=[{'kind':'delete_track','uuid':'feed','reason':'Move matching network'},
                    {'kind':'move_footprint','reference':'L1'}, {'kind':'add_track','net':'RF'}]
        with patch.object(edits,'saved_track_nets',return_value={'feed':'RF'}):
            self.assertEqual(edits.preserve_saved_antenna_feed_tracks(operations,content,'RF'),operations)
            unrelated=[{**op,'reference':'U2'} if op['kind']=='move_footprint' else op for op in operations]
            self.assertEqual(len(edits.preserve_saved_antenna_feed_tracks(unrelated,content,'RF')),2)

    def test_edit_revision_uses_a_fresh_temporary_attempt_and_stops_at_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/('a'*32);folder.mkdir();(folder/'discard').write_text('first failed copy')
            with patch.object(edits,'prepare',return_value={'status':'ready'}) as revise:
                result=edits._revise_from_original(None,None,None,folder,request='RF repair',key='key',model='model',
                    binary='kicad-cli',progress=lambda _:None,approved_design={'antenna_feed_net':'RF'},
                    attempt=0,feedback={'new_findings':[{'rule':'clearance','count':1}]})
            self.assertEqual(result['status'],'ready')
            self.assertFalse(folder.exists())
            self.assertEqual(revise.call_args.kwargs['_repair_attempt'],1)
            self.assertEqual(revise.call_args.kwargs['_validation_feedback']['new_findings'][0]['rule'],'clearance')
            self.assertIsNone(edits._revise_from_original(None,None,None,folder,request='',key='',model='',
                binary='',progress=lambda _:None,approved_design=None,attempt=edits.MAX_DRC_REVISIONS,feedback={}))

    def test_invalid_ops_cannot_escape_interpreter(self):
        for op in [{'kind':'exec','reason':'x'}, {'kind':'move_footprint','reference':'R1','x_mm':float('nan'),'y_mm':1,'rotation_deg':0,'reason':'x'},
                   {'kind':'replace_footprint','reference':'R1','footprint':'../../tmp:a','reason':'x'}]:
            with self.assertRaises(ValueError):edits.validate_operations([op],find_kicad() or '')

    def test_multi_file_rollback_and_modified_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source';folder=root/'plan'
            source.mkdir();(folder/'before').mkdir(parents=True);(folder/'after').mkdir()
            plan={'status':'ready','source':str(source),'files':[],'hashes':{},'file':'a','backup':str(folder/'before'),'change_summary':['Test change']}
            for name in ('a','b'):
                (source/name).write_bytes(b'old');(folder/'before'/name).write_bytes(b'old');(folder/'after'/name).write_bytes(b'new')
                plan['files'].append({'path':name,'original_sha256':fixes.digest(b'old'),'replacement_sha256':fixes.digest(b'new')})
                plan['hashes'][name]=fixes.digest(b'old')
            original=fixes.replace_checked
            def failing(path,content,expected):
                if path.name=='b':raise OSError('Simulated disk error')
                return original(path,content,expected)
            with patch.object(fixes,'replace_checked',side_effect=failing),self.assertRaisesRegex(ValueError,'restored'):
                edits.apply(plan,folder)
            self.assertEqual((source/'a').read_bytes(),b'old')
            self.assertEqual(plan['status'],'ready')
            edits.apply(plan,folder);(source/'b').write_bytes(b'user change')
            with self.assertRaises(ValueError):edits.apply(plan,folder,undo=True)
            self.assertEqual((source/'a').read_bytes(),b'new')


@unittest.skipUnless(RUNTIME.is_file() and find_kicad(),'KiCad runtime required')
class NativeEditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.source=self.root/'source';self.source.mkdir()
        subprocess.run([str(RUNTIME),'-c',CREATE,str(self.source/'demo.kicad_pcb')],capture_output=True,check=True)
        fp='Resistor_SMD:R_0603_1608Metric'
        (self.source/'demo.kicad_sch').write_text(schematic(symbol('R1','10k',fp),symbol('R2','10k',fp)))
        self.project=analyze_project(self.source);self.group={'id':'R1','issue_ids':[]}

    def test_luna_model_is_rejected_without_network_or_source_changes(self):
        from pcb_checker.ai.reviewer import AIError, RoutedKey
        original={p.name:p.read_bytes() for p in self.source.iterdir()}
        with patch('pcb_checker.ai.reviewer.build_opener') as opener:
            with self.assertRaisesRegex(AIError, 'exclusively'):
                edits.prepare(self.project,self.group,self.source,self.root/'luna-preview',
                    request='Move R1 to 10,12 mm',key=RoutedKey('sk-test',backup='gemini-test'),model='gpt-6-luna')
            opener.assert_not_called()
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.source.iterdir()})

    def test_meta_response_produces_checked_edit_preview_without_touching_source(self):
        from test_meta import response, MODEL
        original={p.name:p.read_bytes() for p in self.source.iterdir()}
        operation={k:'' for k in edits.OP_SCHEMA['properties']}
        operation.update(kind='move_footprint',reason='Requested move',reference='R1',x_mm='10',y_mm='12',rotation_deg='0')
        with patch('pcb_checker.ai.reviewer._request',return_value=response(
                {'summary':'Move R1','operations':[operation],'blockers':[]})) as send:
            plan=edits.prepare(self.project,self.group,self.source,self.root/'meta-preview',
                request='Move R1 to 10,12 mm',key='meta-test',model=MODEL)
        self.assertEqual(plan['status'],'ready')
        self.assertTrue(plan['requires_approval'])
        self.assertEqual(plan['operations'][0]['y_mm'],12)
        self.assertEqual(send.call_args.args[3],'meta')
        self.assertEqual(original,{p.name:p.read_bytes() for p in self.source.iterdir()})

    def test_move_draw_replace_preview_apply_and_undo(self):
        original={p.name:p.read_bytes() for p in self.source.iterdir()}
        ops=[{'kind':'move_footprint','reference':'R1','x_mm':10,'y_mm':12,'rotation_deg':90,'reason':'Test placement'},
             {'kind':'replace_footprint','reference':'R2','footprint':'Resistor_SMD:R_0805_2012Metric','reason':'Approved package change'},
             {'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':10,'start_y_mm':12.825,'end_x_mm':18.9875,'end_y_mm':10,'width_mm':.2,'reason':'Reconnect the moved pad'}]
        folder=self.root/'plan'
        plan=edits.prepare(self.project,self.group,self.source,folder,operations=ops,
            approved_design={'reference_antenna_footprints':[{'footprint':'RF_Antenna:Texas_SWRA117D_2.4GHz_Left'}],
                             'antenna_feed_net':'SIGNAL','antenna_ground_net':'GND'})
        self.assertEqual(plan['status'],'ready');self.assertTrue(plan['requires_approval'])
        self.assertEqual({p.name:p.read_bytes() for p in self.source.iterdir()},original)
        self.assertEqual(len(plan['previews']),4)
        for name in plan['previews']:self.assertIn('<svg',(folder/name).read_text())
        edits.apply(plan,folder)
        changed=analyze_project(self.source)
        self.assertEqual(changed.components['R2'].schematic[0].footprint,'Resistor_SMD:R_0805_2012Metric')
        self.assertEqual(changed.components['R2'].pcb[0].footprint,'Resistor_SMD:R_0805_2012Metric')
        self.assertEqual(changed.components['R2'].pcb[0].properties['LCSC'],'C12345')
        self.assertTrue(changed.components['R2'].pcb[0].dnp)
        self.assertFalse(changed.components['R2'].pcb[0].in_bom)
        self.assertEqual(changed.components['R1'].pcb[0].y_mm,12)
        self.assertEqual(changed.components['R1'].pcb[0].rotation,90)
        geom=board_locations((self.source/'demo.kicad_pcb').read_bytes())
        self.assertTrue(any(i['kind']=='track' and i['net']=='SIGNAL' for i in geom['item_locations'].values()))
        edits.apply(plan,folder,undo=True)
        self.assertEqual({p.name:p.read_bytes() for p in self.source.iterdir()},original)

    def test_new_short_blocks_before_write(self):
        original=(self.source/'demo.kicad_pcb').read_bytes()
        op={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':9.175,'start_y_mm':10,'end_x_mm':10.825,'end_y_mm':10,'width_mm':.3,'reason':'Deliberately invalid short'}
        with self.assertRaisesRegex(ValueError,'DRC errors'):
            edits.prepare(self.project,self.group,self.source,self.root/'bad',operations=[op])
        self.assertEqual((self.source/'demo.kicad_pcb').read_bytes(),original)

    def test_autonomous_correction_fixes_new_short_and_keeps_other_move(self):
        original={p.name:p.read_bytes() for p in self.source.iterdir()}
        move={'kind':'move_footprint','reference':'R2','x_mm':20,'y_mm':12,'rotation_deg':0,'reason':'Make room'}
        bad={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':9.175,'start_y_mm':10,
             'end_x_mm':10.825,'end_y_mm':10,'width_mm':.2,'reason':'Deliberately shorted draft'}
        points=[(9.175,10),(9.175,7),(19.175,7),(19.175,12)]
        corrected=[{**bad,'start_x_mm':a[0],'start_y_mm':a[1],'end_x_mm':b[0],'end_y_mm':b[1],
                    'reason':'Route around ground pad'} for a,b in zip(points,points[1:])]
        def response(ops):
            encoded=[{k:str(v) if k.endswith('_mm') or k=='rotation_deg' else v for k,v in op.items()} for op in ops]
            return {'status':'completed','steps':[{'type':'model_output','content':[{'type':'text',
                'text':json.dumps({'summary':'Move and reconnect R2','operations':encoded,'blockers':[]})}]}]}
        messages=[]
        with patch.object(edits,'post_ai',side_effect=[response([move,bad]),response(corrected)]) as ai:
            plan=edits.prepare(self.project,self.group,self.source,self.root/('b'*32),request='Move and reconnect R2',
                               key='test-key',model='test-model',progress=messages.append)
        self.assertEqual(plan['status'],'ready')
        self.assertEqual(ai.call_count,2)
        self.assertTrue(any(op['kind']=='move_footprint' and op['reference']=='R2' for op in plan['operations']))
        feedback=json.loads(ai.call_args.args[0]['input'])['validation_feedback']
        self.assertTrue(feedback['exact_kicad_findings'])
        self.assertTrue(feedback['checked_draft_geometry']['items'])
        self.assertTrue(feedback['attempt_history'])
        stages=[event['stage'] for event in plan['validation_history']]
        self.assertIn('drc_failed',stages)
        self.assertEqual(stages[-1],'ready_for_approval')
        self.assertEqual(plan['validation_history'][-1]['new_error_count'],0)
        self.assertEqual({p.name:p.read_bytes() for p in self.source.iterdir()},original)

    def test_blocker_expands_geometry_instead_of_repeating_same_request(self):
        blocked={'summary':'No initial site','operations':[],'blockers':['R2 blocks the desired placement.']}
        move={'kind':'move_footprint','reference':'R2','x_mm':'22','y_mm':'12','rotation_deg':'0','reason':'Clear space'}
        success={'summary':'Move R2','operations':[move],'blockers':[]}
        replies=[{'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(data)}]}]}
                 for data in (blocked,success)]
        with patch.object(edits,'post_ai',side_effect=replies) as ai:
            result=edits.propose(self.project,self.group,'Clear space',self.source,'key','model',find_kicad())
        packet=json.loads(ai.call_args.args[0]['input'])
        self.assertEqual(packet['relocation_scope']['named_obstacles'],['R2'])
        self.assertTrue(packet['planning_feedback']['history'])
        self.assertTrue(packet['placement_search'][0]['candidates'])
        self.assertEqual(result['operations'][0]['reference'],'R2')

    def test_planning_exhaustion_does_not_multiply_into_drc_retry_loop(self):
        reply={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(
            {'summary':'Blocked','operations':[],'blockers':['Missing a manufacturer requirement.']})}]}]}
        with patch.object(edits,'post_ai',return_value=reply) as ai, self.assertRaises(edits.PlanningIncomplete):
            edits.prepare(self.project,self.group,self.source,self.root/('c'*32),request='Repair',key='key',model='model')
        self.assertEqual(ai.call_count,edits.MAX_LAYOUT_REVIEWS)

    def test_native_track_delete_and_reject_unknown_target(self):
        path=self.source/'demo.kicad_pcb';opfile=self.root/'ops.json'
        script=Path(edits.__file__).with_name('_pcb_edit.py')
        add={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':9.175,'start_y_mm':10,'end_x_mm':19.175,'end_y_mm':10,'width_mm':.2,'reason':'Test route'}
        second={**add,'start_y_mm':11,'end_y_mm':11}
        opfile.write_text(json.dumps([add,second]))
        subprocess.run([str(RUNTIME),str(script),str(path),str(opfile)],capture_output=True,check=True)
        tracks=[i for i in board_locations(path.read_bytes())['item_locations'].values() if i['kind']=='track']
        opfile.write_text(json.dumps([{'kind':'delete_track','uuid':track['uuid'],'reason':'Replace route'} for track in tracks]))
        subprocess.run([str(RUNTIME),str(script),str(path),str(opfile)],capture_output=True,check=True)
        self.assertFalse(any(i['kind']=='track' for i in board_locations(path.read_bytes())['item_locations'].values()))
        before=path.read_bytes()
        result=subprocess.run([str(RUNTIME),str(script),str(path),str(opfile)],capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(path.read_bytes(),before)

    def test_native_reference_antenna_and_copper_keepout(self):
        from pcb_checker.fabrication import prepare_board
        path=self.source/'demo.kicad_pcb';opfile=self.root/'antenna-ops.json'
        ops=[{'kind':'add_reference_antenna','reference':'AE1','footprint':'RF_Antenna:Texas_SWRA117D_2.4GHz_Left',
              'x_mm':20,'y_mm':25,'rotation_deg':0,'net':'SIGNAL','ground_net':'GND','reason':'Test sourced pattern'},
             {'kind':'add_copper_keepout','start_x_mm':7.5,'start_y_mm':19.5,'end_x_mm':23.5,'end_y_mm':24.5,'reason':'Test ground clearance'}]
        opfile.write_text(json.dumps(edits.validate_operations(ops,find_kicad())))
        subprocess.run([str(RUNTIME),str(Path(edits.__file__).with_name('_pcb_edit.py')),str(path),str(opfile)],capture_output=True,check=True)
        prepared=prepare_board(find_kicad(),path,self.root)
        self.assertEqual(prepared['status'],'prepared')
        p=analyze_project(self.source);antenna=p.components['AE1'].pcb[0]
        self.assertFalse(antenna.board_only)
        self.assertTrue(antenna.in_bom)
        self.assertEqual({pad.number:pad.net for pad in antenna.pads},{'1':'SIGNAL','2':'GND'})
        self.assertIn('(copperpour not_allowed)',path.read_text())

    def test_sourced_antenna_with_documented_nc_pad_does_not_get_grounded(self):
        path=self.source/'demo.kicad_pcb';opfile=self.root/'antenna-nc-ops.json'
        footprint='RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz'
        candidate={'footprint':footprint,'manufacturer_part':'2450AT18A0100001E',
                   'pad_roles':{'1':'RF feed','2':'NC; solder for mechanical anchoring'}}
        approved={'antenna_feed_net':'SIGNAL','antenna_ground_net':'GND',
                  'reference_antenna_footprints':[candidate]}
        op={'kind':'add_reference_antenna','reference':'AE1','footprint':footprint,
            'x_mm':25,'y_mm':25,'rotation_deg':0,'net':'SIGNAL','ground_net':'',
            'reason':'Manufacturer datasheet identifies pad 2 as NC.'}
        near_miss={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':19.175,'start_y_mm':10,
                   'end_x_mm':24.75,'end_y_mm':25,'width_mm':.2,'reason':'Route to antenna pad.'}
        edits.align_antenna_feed_endpoints(path.read_bytes(),[op,near_miss],find_kicad())
        self.assertEqual((near_miss['end_x_mm'],near_miss['end_y_mm']),(23.55,25.0))
        self.assertIn('moved 1.20 mm to the exact sourced AE1 pad 1 center',near_miss['reason'])
        # NC must be accepted from the source map, while grounding that same pad is rejected.
        self.assertEqual(edits.validate_operations([op],find_kicad())[0]['ground_net'],'')
        grounded={**op,'ground_net':'GND'}
        # The approved design's GND is the matching-network return, not a
        # request to ground a sourced NC antenna pad. Resolve from pad_roles.
        edits.enforce_antenna_pad_map(grounded,candidate,approved)
        self.assertEqual(grounded['ground_net'],'')
        edits.validate_antenna_pad_map(grounded,candidate)
        edits.validate_antenna_pad_map(op,candidate)
        with self.assertRaisesRegex(ValueError,'does not identify pad 2 as ground'):
            edits.validate_antenna_pad_map({**op,'ground_net':'GND'},candidate)
        prepared_ops=edits.validate_operations([op],find_kicad())
        prepared_ops[0]['target_net']='RF_FEED_RENAMED'
        opfile.write_text(json.dumps(prepared_ops))
        subprocess.run([str(RUNTIME),str(Path(edits.__file__).with_name('_pcb_edit.py')),str(path),str(opfile)],
                       capture_output=True,check=True)
        p=analyze_project(self.source).components['AE1'].pcb[0]
        self.assertEqual({pad.number:pad.net for pad in p.pads},{'1':'RF_FEED_RENAMED','2':''})
        after=analyze_project(self.source)
        self.assertEqual(after.components['R1'].pcb[0].pads[0].net,'RF_FEED_RENAMED')
        self.assertEqual(after.components['AE1'].pcb[0].pads[0].net,'RF_FEED_RENAMED')

    def test_antenna_feed_endpoint_is_snapped_even_when_model_routes_to_body(self):
        path=self.source/'demo.kicad_pcb'
        footprint='RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz'
        antenna={'kind':'add_reference_antenna','reference':'ANT1','footprint':footprint,
                 'x_mm':25,'y_mm':25,'rotation_deg':0,'net':'SIGNAL','ground_net':'',
                 'reason':'Sourced antenna placement'}
        # Pad 1 is at x=23.55 mm. This draft endpoint is 3.5 mm away,
        # a common model mistake when it targets the antenna body instead.
        route={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':19.175,'start_y_mm':10,
               'end_x_mm':20.05,'end_y_mm':25,'width_mm':.2,'reason':'Route to antenna.'}
        edits.align_antenna_feed_endpoints(path.read_bytes(),[antenna,route],find_kicad())
        self.assertAlmostEqual(route['end_x_mm'],23.55,places=3)
        self.assertAlmostEqual(route['end_y_mm'],25,places=3)
        self.assertIn('moved 3.50 mm to the exact sourced ANT1 pad 1 center',route['reason'])

    def test_connected_antenna_route_is_unchanged_on_repeated_alignment(self):
        antenna={'kind':'add_reference_antenna','reference':'ANT1',
                 'footprint':'RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz',
                 'x_mm':25,'y_mm':25,'rotation_deg':0,'net':'SIGNAL','ground_net':'',
                 'reason':'Sourced antenna placement'}
        points=[(19.175,10),(19.175,22),(23.55,22),(23.55,25)]
        routes=[{'kind':'add_track','net':'SIGNAL','layer':'F.Cu',
                 'start_x_mm':a[0],'start_y_mm':a[1],'end_x_mm':b[0],'end_y_mm':b[1],
                 'width_mm':.2,'reason':'Preserve the connected feed'} for a,b in zip(points,points[1:])]
        operations=[antenna,*routes];original=copy.deepcopy(operations)
        for _ in range(2):
            edits.align_antenna_feed_endpoints((self.source/'demo.kicad_pcb').read_bytes(),operations,find_kicad())
            self.assertEqual(operations,original)

    def test_feed_alignment_preserves_source_when_source_is_closer(self):
        antenna={'kind':'add_reference_antenna','reference':'ANT1',
                 'footprint':'RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz',
                 'x_mm':21,'y_mm':12,'rotation_deg':0,'net':'SIGNAL','ground_net':'',
                 'reason':'Sourced antenna placement'}
        route={'kind':'add_track','net':'SIGNAL','layer':'F.Cu','start_x_mm':19.175,'start_y_mm':10,
               'end_x_mm':15,'end_y_mm':15,'width_mm':.2,'reason':'Route to antenna'}
        edits.align_antenna_feed_endpoints((self.source/'demo.kicad_pcb').read_bytes(),[antenna,route],find_kicad())
        self.assertEqual((route['start_x_mm'],route['start_y_mm']),(19.175,10))
        self.assertEqual((route['end_x_mm'],route['end_y_mm']),(19.55,12))

    def test_antenna_file_edit_requires_approved_source_geometry(self):
        op={'kind':'add_reference_antenna','reference':'AE1','footprint':'RF_Antenna:Texas_SWRA117D_2.4GHz_Left',
            'x_mm':20,'y_mm':25,'rotation_deg':0,'net':'SIGNAL','ground_net':'GND','reason':'Test pattern'}
        with self.assertRaisesRegex(ValueError,'approved, sourced design'):
            edits.prepare(self.project,self.group,self.source,self.root/'unapproved-antenna',operations=[op])

    def test_planner_normalizes_decimal_strings_and_ignores_unused_fields(self):
        operation={k:'' for k in edits.OP_SCHEMA['properties']}
        operation.update(kind='move_footprint',reason='Test move',reference='R1',x_mm='10',y_mm='12',rotation_deg='90',layer='F.Cu')
        reply={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps({'summary':'Move R1','operations':[operation],'blockers':[]})}]}]}
        with patch.object(edits,'post_ai',return_value=reply):
            plan=edits.propose(self.project,self.group,'Move R1',self.source,'test-key','test-model',find_kicad())
        self.assertEqual(plan['operations'][0]['y_mm'],12)
        self.assertNotIn('layer',plan['operations'][0])

    def test_batch_requires_explicit_plan_approval(self):
        state=AppState(self.source,self.root/'reports');self.addCleanup(state.close)
        state.project=self.project;state.report_id='review'
        state.store.preference('input:review',{'path':str(self.source)})
        state.file_fixes['test']={'id':'test','status':'ready','report_id':'review','source':str(self.source.resolve()),'requires_approval':True}
        with self.assertRaisesRegex(AppError,'approve every operation'):
            state.apply_file_fix({'fix_id':'test','closed_in_kicad':True})


if __name__=='__main__':unittest.main()
