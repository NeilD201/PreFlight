"""Check calculations, revision binding, output consistency, and temporary rule settings."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.verification import targets, edit, evaluate, calculate, validate_values, placement_check, debug_access
from pcb_checker.native import expanded_rules, NativeError
from pcb_checker.release_review import before_ordering
from pcb_checker.webapp import AppState, AppError, restore_project


def component(name, pins, **flags):
    return {'schematic':[dict(value=name, **flags)],'pcb':[{'pads':[{'number':p[0],'net':p[2]} for p in pins]}],
            'schematic_pins':[dict(number=n,name=label,net=net,electrical_type=kind) for n,label,net,kind in pins]}


def design():
    return {'archive':'controller', 'provenance':{'source_hashes':{'board.kicad_pcb':'hash1'}},
            'selected_files':{'pcb':'board.kicad_pcb'}, 'components':{
                'U1':component('SENSOR',[('1','VDD','SUPPLY','power_in'),('2','VDD','SUPPLY','power_in')]),
                'U2':component('OTHER',[('1','VDD','SUPPLY','power_in'),('2','GND','GND','power_in')]),
                'U3':component('DNP',[('1','VDD','SUPPLY','power_in')], dnp=True)},
            'circuit':{'bus_candidates':[{'net':'SDA','pullups':[{'ohms':1500}],
                       'parallel_pullups':[{'rail':'SUPPLY','ohms':1500}]}]}}


class CalculationTests(unittest.TestCase):
    def setUp(self):
        self.data=design();self.bus=targets(self.data)[0]
        self.values={'voltage':3.3,'tolerance':0,'capacitance':200,'rise':300,'sink':3,'low':.4}

    def test_published_rc_example_and_sink_limit(self):
        r=calculate(self.bus,self.values)
        self.assertEqual(r['status'],'checked')
        self.assertAlmostEqual(r['metrics']['rise_ns'],254.19)
        self.assertAlmostEqual(r['metrics']['sink_ma'],1.93333333)
        for v in ({'rise':120},{'sink':1},{'tolerance':20}):
            self.assertEqual(calculate(self.bus,self.values|v)['status'],'attention')

    def test_no_bus_pass_without_every_limit_and_source(self):
        self.assertEqual(evaluate(self.data)['calculations'][0]['status'],'missing')
        for values,note in [(self.values,''),({k:v for k,v in self.values.items() if k!='sink'},'datasheet')]:
            with self.assertRaises(ValueError):validate_values(self.bus,values,note)

    def test_nonphysical_and_nonfinite_inputs_rejected(self):
        for field,value in [('voltage',True),('capacitance',float('nan')),('sink',float('inf')),
                            ('rise',0),('low',3.3),('tolerance',100),('sink',-1)]:
            with self.assertRaises(ValueError):validate_values(self.bus,self.values|{field:value},'datasheet')

    def test_revisions_invalidate_prior_calculations_and_preserve_inputs(self):
        request=dict(target=self.bus['key'],signature=self.bus['signature'],values=self.values,note='device limits')
        self.data['verification']=edit(self.data,request,{})
        self.assertEqual(evaluate(self.data)['calculations'][0]['status'],'checked')
        self.data['provenance']['source_hashes']['board.kicad_pcb']='hash2'
        result=evaluate(self.data)['calculations'][0]
        self.assertEqual(result['status'],'missing');self.assertTrue(result['stale']);self.assertEqual(result['values'],self.values)
        with self.assertRaises(ValueError):edit(self.data,request,self.data['verification'])

    def test_rail_deduplicates_supply_pins_and_excludes_dnp_and_ground(self):
        rail=targets(self.data)[1]
        self.assertEqual(rail['loads'],{'U1':'SENSOR','U2':'OTHER'})
        values={'available':100,'extra':5,'reserve':20,'load_0':50,'load_1':25}
        result=calculate(rail,values)
        self.assertEqual(result['status'],'checked');self.assertEqual(result['metrics']['margin_ma'],0)
        self.assertEqual(calculate(rail,values|{'load_1':26})['status'],'attention')
        self.assertEqual(len([t for t in targets(self.data) if t['kind'] in ('i2c','power')]),2)

    def test_multi_rail_pullups_or_unreadable_resistor_not_calculated(self):
        bus=self.data['circuit']['bus_candidates'][0]
        bus['pullups'].append({'ohms':None})
        self.assertFalse(any(t['kind']=='i2c' for t in targets(self.data)))
        bus['pullups'].pop();bus['parallel_pullups'].append({'rail':'OTHER','ohms':1000})
        self.assertFalse(any(t['kind']=='i2c' for t in targets(self.data)))

    def test_grounded_ep_and_supply_pin_do_not_create_ground_budget(self):
        self.data['components']['U4']=component('MCU',[('1','VBUS','GND','power_in'),('2','V_{SS}','GND','power_in'),('3','EP','GND','power_in'),('4','VDD','SUPPLY','bidirectional')])
        rails=[t for t in targets(self.data) if t['kind']=='power']
        self.assertEqual([t['label'] for t in rails],['SUPPLY'])
        self.assertIn('U4',rails[0]['loads'])

    def test_save_limits_persistence_stale_tab_and_wrong_report(self):
        with tempfile.TemporaryDirectory() as temp:
            state=AppState(None,Path(temp)/'reports')
            try:
                data=deepcopy(self.data)
                for ref,c in data['components'].items():
                    c['reference']=ref
                    for record in c['schematic']+c['pcb']:
                        record.update(reference=ref,source_file='board',line=1,footprint='')
                        record.setdefault('value','SENSOR')
                        for pad in record.get('pads',[]):pad['line']=1
                state.project=restore_project(data);state.report_id='current'
                self.bus=targets(data)[0]
                request=dict(report_id='current',revision=0,target=self.bus['key'],signature=self.bus['signature'],values=self.values,note='device limits')
                state.save_verification(request)
                with self.assertRaises(AppError):state.save_verification(request)
                with self.assertRaises(AppError):state.save_verification(request|{'report_id':'old','revision':1})
                from pcb_checker.design_review import context_key
                saved=state.store.design_context('verification:'+context_key(state.project))
                self.assertEqual(saved['revision'],1)
                state.save_verification(request|{'revision':1,'clear':True})
                self.assertFalse(state.project.verification['entries'])
                # Context can change without rerunning the saved board review.
                # Both the form signature and save validation must use that live context.
                from dataclasses import asdict
                context=state.store.save_context(context_key(state.project),{'description':'Sealed enclosure'},0)
                live=asdict(state.project)|{'design_context':context}
                thermal=next(t for t in targets(live) if t['kind']=='thermal')
                state.save_verification(dict(report_id='current',revision=2,target=thermal['key'],signature=thermal['signature'],
                    values={'ambient':40,'loss':.5,'theta':80,'maximum':85},note='Assembly-specific model'))
                shown=state.project_data()
                result=next(c for c in shown['verification_results']['calculations'] if c['key']==thermal['key'])
                self.assertEqual(result['status'],'checked')
                self.assertEqual(next(r for r in shown['before_ordering']['checks'] if r['id']=='thermal')['status'],'partial')
            finally:state.close()


class FileChecks(unittest.TestCase):
    def board(self):
        data=design();data['assembly_export']={'status':'draft',
            'placement':{'status':'generated','pcb_sha256':'hash1'},
            'parts':[{'reference':'U1'},{'reference':'J1','placement_excluded':True}],
            'bom':[{'references':['U1']},{'references':['J1']}],
            'cpl':[{'reference':'U1','x_mm':1.,'y_mm':-3.,'rotation':90.,'side':'bottom'}]}
        return data

    def test_complete_population_with_intentional_pos_exclusion_passes(self):
        self.assertEqual(placement_check(self.board())[0],'checked')

    def test_duplicates_missing_rows_wrong_bom_invalid_side_or_coordinates_fail(self):
        for change in ('duplicate','missing','bom','side','nan'):
            d=self.board();a=d['assembly_export']
            if change=='duplicate':a['cpl']*=2
            if change=='missing':a['cpl']=[]
            if change=='bom':a['bom']=[]
            if change=='side':a['cpl'][0]['side']='unknown'
            if change=='nan':a['cpl'][0]['x_mm']=float('nan')
            self.assertEqual(placement_check(d)[0],'attention',change)

    def test_changed_pcb_cannot_use_previous_placements(self):
        d=self.board();d['provenance']['source_hashes']['board.kicad_pcb']='new'
        self.assertEqual(placement_check(d)[0],'missing')

    def test_debug_requires_both_signals_and_matching_real_board_pads(self):
        d={'components':{'U1':component('MCU',[('1','SWDIO','DATA','bidirectional'),('2','SWDCLK','CLOCK','input')]),
                         'J1':component('DEBUG',[('1','Pin_1','DATA','passive'),('2','Pin_2','CLOCK','passive')])}}
        self.assertEqual(debug_access(d)[0],'checked')
        d['components']['J1']['pcb'][0]['pads'][1]['net']='DIFFERENT'
        self.assertEqual(debug_access(d)[0],'partial')
        d['components']['U1']['schematic_pins'][1]['net']='DATA'
        self.assertEqual(debug_access(d)[0],'partial')


class ExpandedRules(unittest.TestCase):
    def test_failed_or_successful_rerun_restores_original_settings(self):
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as temp:
                d=Path(temp);p=d/'b.kicad_pro';original=b'{"board":{"design_settings":{"rule_severities":{"missing_courtyard":"ignore"}}}}'
                p.write_bytes(original);reports={}
                def run(binary,args,directory):
                    self.assertEqual(json.loads(p.read_bytes())['board']['design_settings']['rule_severities']['missing_courtyard'],'warning')
                    if fail:raise NativeError('fake failure')
                    Path(args[args.index('--output')+1]).write_text(json.dumps({'violations':[{'type':'missing_courtyard','severity':'warning'}]}))
                with patch('pcb_checker.native._run',side_effect=run):
                    result=expanded_rules('kicad',[('drc',['pcb','drc'],str(d/'b.kicad_pcb'),'json')],d,reports)
                self.assertEqual(p.read_bytes(),original)
                self.assertEqual(result['status'],'failed' if fail else 'completed')
                if not fail:self.assertEqual(result['results'][0]['findings'],[{'rule':'missing_courtyard','count':1}])

    def test_rerun_findings_are_review_not_a_pass_or_hardware_fault(self):
        d={'board_checks':{'rule_settings':{'status':'read','ignored_drc':['missing_courtyard']},
                          'expanded_rule_review':{'status':'completed','results':[{'kind':'drc','findings':[{'rule':'missing_courtyard','count':2}]}]}}}
        rows={r['id']:r for r in before_ordering(d)['checks']}
        self.assertEqual(rows['rule_scope']['status'],'attention')
        self.assertIn('2 findings',rows['rule_scope']['evidence'][0])


if __name__=='__main__':unittest.main()
