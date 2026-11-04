"""Process boundaries, honest coverage and project-scoped manufacturer choices."""
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.manufacturing import evaluate, measure_fabrication, measure_source, validate_selection
from pcb_checker.parsers.sexpr import parse
from pcb_checker.design_review import current_plan
from pcb_checker.review_sections import organize_review
from pcb_checker.webapp import AppState, AppError
from test_footprint_context import example

BOARD='''(kicad_pcb (general (thickness 1.6)) (layers (0 "F.Cu" signal) (2 "In1.Cu" signal) (4 "In2.Cu" signal) (31 "B.Cu" signal))
(setup (stackup (layer "F.Cu" (type copper) (thickness 0.035)) (layer "dielectric 1" (type core) (material FR4))
(layer "In1.Cu" (type copper) (thickness 0.035)) (layer "In2.Cu" (type copper) (thickness 0.035)) (layer "B.Cu" (type copper) (thickness 0.035))))
(segment (start 0 0) (end 10 0) (width 0.10) (layer "F.Cu"))
(arc (start 10 0) (mid 11 1) (end 10 2) (width 0.09) (layer "F.Cu"))
(via (at 0 0) (size 0.25) (drill 0.15) (layers "F.Cu" "B.Cu")))'''

def measured():
    return measure_fabrication(parse(BOARD, 'kicad_pcb'))

def check(maker='jlcpcb', m=None, data=None, **options):
    return evaluate(data or {}, {'manufacturer': maker, **options}, m or measured())

def row(result, cid):
    return next(r for r in result['checks'] if r['id'] == cid)

class ManufacturingTests(unittest.TestCase):
    def test_via_in_pad_uses_drill_overlap_not_annular_ring(self):
        def m(x, shape='rect', layers='F.Cu F.Mask F.Paste'):
            text=BOARD.replace('(via (at 0 0)',f'(via (at {x} 0)')
            text=text[:-1]+f'(footprint "test" (at 0 0) (property "Reference" "U1") (pad "1" smd {shape} (at 0 0) (size 1 1) (layers {layers}))))'
            return measure_fabrication(parse(text,'kicad_pcb'))
        self.assertEqual(m(0)['via_in_pad']['count'],1)
        self.assertEqual(m(.56)['via_in_pad']['count'],1)  # hole clips pad edge
        self.assertEqual(m(.6)['via_in_pad']['count'],0)   # only copper ring overlaps
        self.assertEqual(m(0,layers='B.Cu B.Mask B.Paste')['via_in_pad']['count'],1)
        self.assertEqual(m(0,layers='F.Cu')['via_in_pad']['count'],0)
        self.assertEqual(m(0,shape='custom')['via_in_pad']['status'],'partial')
        jlc=check(m=m(0));way=check('pcbway',m=m(0))
        self.assertEqual(row(jlc,'via_in_pad')['status'],'option')
        self.assertTrue(any('Epoxy-Filled & Capped' in s for s in jlc['order_settings']))
        self.assertIn('copper capped',row(way,'via_in_pad')['advice'])

    def test_rotated_back_pad_and_internal_via_layers(self):
        text=BOARD.replace('(at 0 0) (size 0.25)', '(at 10 8) (size 0.25)')
        text=text[:-1]+'''(footprint "test" (layer B.Cu) (at 10 10 90) (property "Reference" "U1")
        (pad "1" smd roundrect (at 2 0 90) (size 2 1) (roundrect_rratio 0.25) (layers B.Cu B.Mask B.Paste))))'''
        self.assertEqual(measure_fabrication(parse(text,'kicad_pcb'))['via_in_pad']['count'],1)
        text=text.replace('(layers "F.Cu" "B.Cu")','(layers "In1.Cu" "In2.Cu")')
        self.assertEqual(measure_fabrication(parse(text,'kicad_pcb'))['via_in_pad']['count'],0)

    def test_filling_advice_survives_small_via_summary_and_ai_packet(self):
        from pcb_checker.manufacturing import compact_evidence
        p=example();p.issues[0].code='KICAD_DRC_VIA_DIAMETER'
        p.issues[0].items=[{'description':'Via [GND] on F.Cu - B.Cu'}]
        m=measured();m['via_in_pad']={'status':'measured','count':1,'locations':[
            {'reference':'U1','pad':'1','drill_mm':.15,'via_uuid':'v','pad_uuid':'p'}]}
        p.manufacturing=check(data=asdict(p),m=m)
        action=current_plan(p)['actions'][0]
        self.assertIn('filling and capping',action['title'])
        self.assertIn('Epoxy-Filled & Capped',action['steps'][0])
        self.assertTrue(any('via-size' in step for step in action['steps']))
        self.assertTrue(any('Epoxy-Filled & Capped' in step for step in action['steps']))
        self.assertIn('solder away',action['consequence']['text'])
        self.assertEqual(action['action_kind'], 'order_setting')
        self.assertTrue(any('Confirm production files' in step for step in action['steps']))
        self.assertFalse(any('move the via' in step for step in action['steps']))
        # Reclassify saved advice when switching to a process that cannot make it.
        p.action_plan = current_plan(p)
        p.manufacturing=check('pcbway',data=asdict(p),m=m)
        self.assertNotEqual(current_plan(p)['actions'][0]['action_kind'], 'order_setting')
        packet=compact_evidence(p.manufacturing)
        self.assertIn('copper capped',str(packet))

    def test_order_requirement_rejects_repair_before_ai_or_files(self):
        p=example();p.issues[0].code='KICAD_DRC_VIA_DIAMETER'
        p.issues[0].items=[{'description':'Via [GND] on F.Cu - B.Cu'}]
        p.manufacturing=check(data=asdict(p))
        with tempfile.TemporaryDirectory() as folder:
            state=AppState(folder,Path(folder)/'reports',ai=False)
            try:
                state.project=p;state.report_id='run1'
                with self.assertRaisesRegex(AppError,'manufacturer order setting'):
                    state.prepare_file_fix({'report_id':'run1','group_id':'R001','edit_request':'Fix this issue'})
                self.assertFalse(state.busy())
                self.assertEqual(state.file_fixes,{})
            finally:state.close()

    def test_reads_actual_dimensions_including_arcs(self):
        m=measured()
        self.assertEqual((m['layers'],m['thickness_mm'],m['min_trace_mm']),(4,1.6,.09))
        self.assertEqual(m['vias'][0]['ring_mm'],.05)
        self.assertEqual(m['trace_count'],2)

    def test_same_via_needs_jlc_option_but_fails_pcbway_standard(self):
        jlc=check();way=check('pcbway')
        self.assertEqual(row(jlc,'via_0')['status'],'option')
        self.assertIn('adds cost',row(jlc,'via_0')['advice'])
        self.assertEqual(row(way,'via_0')['status'],'outside')
        self.assertIn('0.45 mm',row(way,'via_0')['advice'])
        self.assertTrue(any('via-size' in s for s in jlc['order_settings']))

    def test_trace_minimum_depends_on_supplier_layers_and_copper(self):
        self.assertEqual(row(check(),'trace_width')['status'],'supported')
        self.assertEqual(row(check('pcbway'),'trace_width')['status'],'outside')
        m=measured();m['layers']=2
        self.assertEqual(row(check(m=m),'trace_width')['status'],'outside')
        self.assertEqual(row(check(copper_oz='2'),'trace_width')['status'],'outside')
        self.assertEqual(row(check('pcbway',copper_oz='2'),'trace_width')['status'],'unknown')
        self.assertEqual(row(check(copper_oz='2'),'copper')['status'],'unknown')

    def test_tiny_holes_require_thin_board_and_compatible_finish(self):
        m=measured();m['vias'][0].update(drill_mm=.1,diameter_mm=.2,ring_mm=.05)
        self.assertEqual(row(check(m=m),'via_0')['status'],'outside')
        m['thickness_mm']=1
        self.assertEqual(row(check(m=m),'via_0')['status'],'option')
        self.assertTrue(any('drill: 0.1 mm' in s for s in check(m=m)['kicad_settings']))
        self.assertEqual(row(check(m=m,finish='hasl'),'via_0')['status'],'outside')
        m['vias'][0]['ring_mm']=.049
        self.assertEqual(row(check(m=m),'via_0')['status'],'outside')

    def test_unverified_via_types_and_missing_geometry_never_pass(self):
        m=measured();m['vias'][0]['through']=False
        self.assertEqual(row(check(m=m),'via_0')['status'],'unknown')
        self.assertEqual(check(m={'status':'unavailable','message':'Click Update'})['status'],'unavailable')
        m=measured();m['copper_mm']=[];m['materials']=[];m['min_trace_mm']=None
        for cid in ('copper','material','trace_width'):
            self.assertEqual(row(check(m=m),cid)['status'],'unknown')

    def test_hasl_not_offered_for_jlc_six_layer_or_thin_board(self):
        for field,value in [('layers',6),('thickness_mm',.4)]:
            m=measured();m[field]=value
            self.assertEqual(row(check(m=m,finish='hasl'),'finish')['status'],'outside')
            self.assertEqual(row(check(m=m,finish='enig'),'finish')['status'],'supported')

    def test_pad_gap_supported_or_actionable_by_selected_process(self):
        p=example();p.issues[0].message=p.issues[0].message.replace('0.1500','0.1400')
        p.manufacturing=check(data=asdict(p))
        plan=current_plan(p);a=plan['actions'][0]
        self.assertEqual(a['manufacturer_check']['status'],'outside')
        self.assertFalse(a['reference_only'])
        self.assertNotIn('footprint_context',a)
        data=asdict(p);data['action_plan']=plan
        assignment=organize_review(data)['assignments']['R001']
        self.assertEqual(assignment['section'],'manufacturing')
        self.assertTrue(assignment['attention'])
        p.manufacturing=check('pcbway',data=asdict(p))
        self.assertEqual(current_plan(p)['actions'][0]['disposition'],'no_change')

    def test_component_hole_findings_do_not_use_via_limits(self):
        p=example();p.issues[0].code='KICAD_DRC_ANNULAR_WIDTH'
        p.manufacturing=check(data=asdict(p))
        self.assertFalse(any(c['group_ids'] for c in p.manufacturing['checks'] if c['id'].startswith('via_')))
        p.issues[0].items=[{'description':'Via [GND] on F.Cu - B.Cu'}]
        p.manufacturing=check(data=asdict(p))
        self.assertEqual(row(p.manufacturing,'via_0')['group_ids'],['R001'])

    def test_clearing_selection_drops_old_manufacturer_advice(self):
        p=example();p.manufacturing=check(data=asdict(p));p.action_plan=current_plan(p)
        p.manufacturing=evaluate(asdict(p),{},measured())
        self.assertNotIn('manufacturer_check',current_plan(p)['actions'][0])

    def test_source_must_match_checked_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.kicad_pcb';path.write_text(BOARD)
            p=example();p.selected_files={'pcb':path.name};p.provenance={'source_hashes':{path.name:sha256(path.read_bytes()).hexdigest()}}
            self.assertEqual(measure_source(p,folder)['status'],'measured')
            path.write_text(BOARD+'\n')
            self.assertIn('changed',measure_source(p,folder)['message'])

    def test_input_validation(self):
        for selection in ({'manufacturer':'unknown'}, {'finish':'foo'}, {'copper_oz':1}, [], {'manufacturer':[]}):
            with self.assertRaises((ValueError,TypeError)):validate_selection(selection)

    def test_selection_persists_per_project_and_cache_is_per_run(self):
        with tempfile.TemporaryDirectory() as folder:
            state=AppState(folder,Path(folder)/'reports',ai=False)
            try:
                p=example();p.provenance['schematic_uuid']='project-1';state.project=p;state.report_id='run1'
                with patch('pcb_checker.webapp.measure_source',return_value=measured()) as measure:
                    result=state.select_manufacturer({'report_id':'run1','selection':{'manufacturer':'jlcpcb'}})
                    self.assertEqual(result['selection']['manufacturer'],'jlcpcb')
                    state.manufacturing_result(p,'run1',measure=True);self.assertEqual(measure.call_count,1)
                    state.manufacturing_result(p,'run2',measure=True);self.assertEqual(measure.call_count,2)
                other=example();other.provenance['schematic_uuid']='project-2'
                self.assertEqual(state.manufacturing_result(other)['status'],'not_selected')
                p.manufacturing={}
                self.assertEqual(state.manufacturing_result(p,'run1')['selection']['manufacturer'],'jlcpcb')
                with self.assertRaises(AppError):state.select_manufacturer({'report_id':'old','selection':{}})
            finally:state.close()

    def test_old_measurements_upgrade_and_do_not_hide_cached_overlap(self):
        with tempfile.TemporaryDirectory() as folder:
            state=AppState(folder,Path(folder)/'reports',ai=False)
            try:
                p=example();old=measured();old['version']=1;old.pop('via_in_pad')
                p.signal_paths={'board_measurements':{'fabrication':old}}
                with patch('pcb_checker.webapp.measure_source',return_value=measured()) as measure:
                    state.manufacturing_result(p,'run1',measure=True)
                    result=state.manufacturing_result(p,'run1')
                    self.assertEqual(measure.call_count,1)
                    self.assertEqual(result['measurements']['version'],2)
                    self.assertIn('via_in_pad',result['measurements'])
            finally:state.close()

if __name__=='__main__':unittest.main()
