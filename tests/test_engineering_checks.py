"""Analytical examples, invalidation and conservative geometry coverage."""
import hashlib
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.verification import targets, calculate, validate_values, edit, evaluate, compact_evidence
from pcb_checker.routing_geometry import covered_intervals, union_fraction, parallel_pair, measure
from pcb_checker.parsers.sexpr import parse
from pcb_checker.webapp import AppState, AppError
from pcb_checker.models import Project


def design():
    return {'archive':'test','provenance':{'source_hashes':{'board.kicad_pcb':'original'}},'design_context':{},
        'components':{'U1':{'schematic':[{'value':'MCU'}], 'schematic_pins':[{'number':'1','name':'VDD','net':'VCC','electrical_type':'power_in'}]},
                      'J1':{'schematic':[{'value':'CONNECTOR'}],'schematic_pins':[{'number':'1','name':'PIN','net':'IO'}]},
                      'U2':{'schematic':[{'value':'UNUSED','dnp':True}]}},
        'signal_paths':{'board_measurements':{'nets':[{'net':'IO','dc_trace':{'status':'estimated','ohms':.1,'layer':'F.Cu','endpoints_mm':[[0,0],[1,0]]}}]}}}


class EngineeringTests(unittest.TestCase):
    def target(self,kind):return next(t for t in targets(design()) if t['kind']==kind)

    def test_thermal_model_and_margin(self):
        t=self.target('thermal');v={'ambient':40,'loss':.5,'theta':80,'maximum':85}
        self.assertEqual(calculate(t,v)['metrics']['junction_c'],80)
        self.assertEqual(calculate(t,v)['status'],'checked')
        self.assertEqual(calculate(t,v|{'loss':1})['status'],'attention')
        with self.assertRaises(ValueError):validate_values(t,v|{'maximum':30},'thermal model')
        self.assertNotIn('thermal:U2',[t['key'] for t in targets(design())])

    def test_load_step_units_and_all_terms(self):
        t=self.target('transient');v={'current':.1,'duration':100,'capacitance':100,'esr':50,'inductance':2,'rise':100,'maximum':.11}
        r=calculate(t,v)
        self.assertAlmostEqual(r['metrics']['drop_v'],.107)
        self.assertEqual(r['status'],'checked')
        self.assertEqual(calculate(t,v|{'maximum':.1})['status'],'attention')
        for key in ('duration','capacitance','rise'):
            with self.assertRaises(ValueError):validate_values(t,v|{key:0},'load step')

    def test_esd_includes_layout_inductance_and_normal_voltage(self):
        t=self.target('esd_margin');v={'normal':3.3,'standoff':5,'clamp':10,'inductance':.25,'slew':40,'maximum':21}
        r=calculate(t,v)
        self.assertEqual(r['metrics']['stress_v'],20)
        self.assertEqual(r['status'],'checked')
        self.assertEqual(calculate(t,v|{'maximum':19})['status'],'attention')
        self.assertEqual(calculate(t,v|{'normal':6})['status'],'attention')
        with self.assertRaises(ValueError):validate_values(t,v|{'clamp':4},'matched pulse')

    def test_trace_loss_endpoints_not_ampacity(self):
        t=self.target('trace_load');v={'current':2,'resistance_factor':1.5,'maximum_drop':.4,'maximum_loss':.7}
        r=calculate(t,v)
        self.assertAlmostEqual(r['metrics']['drop_v'],.3)
        self.assertAlmostEqual(r['metrics']['loss_w'],.6)
        self.assertEqual(calculate(t,v|{'maximum_loss':.5})['status'],'attention')
        with self.assertRaises(ValueError):validate_values(t,v|{'resistance_factor':.5},'worst case')

    def test_missing_and_changed_operating_context_never_pass(self):
        d=design();t=self.target('thermal');v={'ambient':40,'loss':.5,'theta':80,'maximum':85}
        self.assertTrue(all(r['status']=='missing' for r in evaluate(d)['calculations']))
        d['verification']=edit(d,{'target':t['key'],'signature':t['signature'],'values':v,'note':'measured enclosure'}, {})
        self.assertEqual(compact_evidence(d)['results'][0]['metrics']['junction_c'],80)
        d['design_context']={'description':'Different enclosure'}
        self.assertFalse(compact_evidence(d)['results'])
        row=next(r for r in evaluate(d)['calculations'] if r['key']==t['key'])
        self.assertTrue(row['stale'])
        self.assertEqual(row['values'],v)

    def test_all_models_reject_nonfinite_or_incomplete_inputs(self):
        for t in targets(design()):
            values={f['key']:max(1,f['minimum']) for f in t['fields']}
            for bad in (float('nan'),float('inf'),True):
                with self.subTest(kind=t['kind'],bad=bad),self.assertRaises(ValueError):
                    validate_values(t,values|{t['fields'][0]['key']:bad},'source')
            with self.assertRaises(ValueError):validate_values(t,{},'source')


BOARD='''(kicad_pcb (layers (0 "F.Cu" signal) (1 "In1.Cu" power) (31 "B.Cu" signal))
(net 1 "SIGNAL") (net 2 "NEIGHBOR") (net 3 "GND")
(segment (start 0 0) (end 10 0) (width .2) (layer "F.Cu") (net 1) (uuid "signal"))
(segment (start 0 .5) (end 10 .5) (width .2) (layer "F.Cu") (net 2) (uuid "neighbor"))
(zone (net 3) (layer "In1.Cu")
 (filled_polygon (layer "In1.Cu") (pts (xy -1 -1) (xy 4 -1) (xy 4 2) (xy -1 2)))
 (filled_polygon (layer "In1.Cu") (pts (xy 6 -1) (xy 11 -1) (xy 11 2) (xy 6 2)))))'''


class GeometryTests(unittest.TestCase):
    def test_exact_centerline_gap_not_sampling(self):
        r=measure(parse(BOARD,'kicad_pcb'))
        self.assertEqual(r['status'],'measured')
        self.assertEqual(r['parallel_count'],1)
        self.assertAlmostEqual(r['parallel_pairs'][0]['gap_mm'],.3)
        for row in r['reference_coverage']:self.assertAlmostEqual(row['uncovered_mm'],2)
        self.assertEqual(r['reference_coverage'][0]['gaps'][0]['uuid'],'signal')

    def test_union_does_not_double_count_overlapping_fills(self):
        self.assertEqual(union_fraction([(0,.6),(.4,.8),(.8,1)]),1)

    def test_sub_micron_slit_is_not_lost_to_sampling(self):
        a=(0,0);b=(10,0)
        p1=[(-1,-1),(4.99999,-1),(4.99999,1),(-1,1)]
        p2=[(5.00001,-1),(11,-1),(11,1),(5.00001,1)]
        fraction=union_fraction(covered_intervals(a,b,p1)+covered_intervals(a,b,p2))
        self.assertAlmostEqual(10*(1-fraction),.00002,places=8)

    def test_hole_bridge_and_collinear_edge(self):
        # Standard bridge representation from outer outline to the hole.
        poly=[(0,0),(10,0),(10,10),(0,10),(0,0),(3,3),(3,7),(7,7),(7,3),(3,3),(0,0)]
        self.assertAlmostEqual(union_fraction(covered_intervals((0,5),(10,5),poly)),.6)
        self.assertAlmostEqual(union_fraction(covered_intervals((0,0),(10,0),poly)),1)

    def test_no_ground_and_arc_are_not_passes(self):
        r=measure(parse('(kicad_pcb (arc))','kicad_pcb'))
        self.assertEqual(r['status'],'partial');self.assertEqual(r['reference_coverage'],[])
        self.assertNotEqual(r['status'],'checked')

    def test_different_layers_same_net_and_crossings_are_not_parallel_candidates(self):
        a={'start':[0,0],'end':[10,0],'net':'A','layer':'F.Cu','width_mm':.2}
        for b in (a|{'net':'B','layer':'B.Cu'},a|{'start':[0,.5],'end':[10,.5]},a|{'net':'B','end':[0,10]}):
            self.assertIsNone(parallel_pair(a,b))

    def test_measurement_endpoint_requires_current_snapshot_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);run=root/'run1';run.mkdir();path=run/'analyzed-board.kicad_pcb';path.write_text(BOARD)
            state=AppState(None,root,ai=False)
            try:
                state.project=Project(archive='test');state.report_id='run1'
                state.project.board_checks={'status':'prepared','analyzed_pcb_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                with self.assertRaises(AppError):state.measure_engineering_geometry({'report_id':'old'})
                self.assertEqual(state.measure_engineering_geometry({'report_id':'run1'})['status'],'measured')
                path.write_text(BOARD+'\n')
                with self.assertRaises(AppError):state.measure_engineering_geometry({'report_id':'run1'})
                self.assertFalse(state.busy())
            finally:state.close()


if __name__=='__main__':unittest.main()
