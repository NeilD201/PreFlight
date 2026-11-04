"""Datasheet checks must distinguish a directly observed conflict from missing context."""
import copy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.models import Project,Pin
from pcb_checker.checks.schematic import schematic_checks
from pcb_checker.review import build_review
from pcb_checker.workflow import compare_runs
from test_review import component
from test_design_review import with_doc,project,design_result
from pcb_checker.ai.reviewer import evidence_packet,validate_review,allowed_evidence,DESIGN_SCHEMA,AIError
from pcb_checker.design_review import compose_plan


def charger():
    p=Project('charger')
    p.components['U1']=component('U1','MCP73831-2-MC',[Pin('1',net='USB'),Pin('3',net='BAT'),Pin('6',net='RETURN'),Pin('8',net='PROG')])
    for ref,value,a,b in [('C1','4.7u','USB','RETURN'),('C2','4.7u','BAT','RETURN'),('R1','10k','PROG','RETURN')]:
        p.components[ref]=component(ref,value,[Pin('1',net=a),Pin('2',net=b)])
    p.provenance={'schematic_uuid':'same','source_hashes':{'board':'old'}}
    return p


class SchematicTests(unittest.TestCase):
    def test_supported_connections_and_nominal_charge_current(self):
        findings,checks=schematic_checks(charger())
        self.assertFalse(findings)
        self.assertTrue(any('100 mA' in c['detail'] for c in checks))

    def test_wrong_capacitor_connection_and_dnp_are_not_counted(self):
        for mode in ('wrong_net','not_fitted','too_small','unknown'):
            p=charger();c=p.components['C1']
            if mode=='wrong_net':c.schematic_pins[1].net='OTHER'
            if mode=='not_fitted':c.schematic[0].dnp=True
            if mode in ('too_small','unknown'):c.schematic[0].value='100n' if mode=='too_small' else '?'
            findings,_=schematic_checks(p)
            self.assertTrue(any(f['rule']=='schematic:vdd_capacitor' for f in findings),mode)
            self.assertTrue(all(f['priority']=='review' for f in findings))

    def test_supply_to_ground_is_observed_conflict(self):
        p=charger();p.components['U1'].schematic_pins[0].net='RETURN'
        findings,_=schematic_checks(p)
        self.assertTrue(any(f['rule']=='schematic:supply_ground' and f['priority']=='fix' for f in findings))

    def test_floating_prog_requires_use_context(self):
        p=charger();del p.components['R1']
        findings,_=schematic_checks(p)
        finding=next(f for f in findings if f['rule']=='schematic:charge_resistor')
        self.assertEqual(finding['priority'],'review');self.assertIn('intentional',finding['why'])

    def test_wrong_package_or_dnp_chip_is_not_checked(self):
        for mode in ('package','dnp'):
            p=charger()
            for r in p.components['U1'].schematic+p.components['U1'].pcb:
                if mode=='package':r.value='MCP73831-2-OT'
                else:r.dnp=True
            self.assertEqual(schematic_checks(p),([],[]))

    def test_pulse_sensor_reports_missing_caps_and_pullup_as_review(self):
        p=Project('sensor');p.components['U1']=component('U1','MAX30102EFD+T',
            [Pin('3',net='DATA'),Pin('4',net='RETURN'),Pin('9',net='LED'),Pin('11',net='ANALOG'),Pin('12',net='RETURN')])
        findings,checks=schematic_checks(p)
        self.assertEqual({f['rule'] for f in findings},{'schematic:vdd_capacitor','schematic:vled_capacitor','schematic:sda_pullup'})
        self.assertTrue(all(f['priority']=='review' for f in findings))

    def test_removed_pin_cannot_clear_previous_capacitor_warning(self):
        p=charger();del p.components['C1'];p.review=build_review(p)
        before=asdict(p)
        p.components['U1'].schematic_pins=[pin for pin in p.components['U1'].schematic_pins if pin.number!='1']
        p.provenance['source_hashes']['board']='new';p.review=build_review(p)
        diff=compare_runs(before,asdict(p))
        item=next(i for i in diff['progress']['items'] if 'VDD capacitor' in i['title'])
        self.assertEqual(item['status'],'unverified')


class NewAIFindingsTests(unittest.TestCase):
    def setUp(self):
        self.p=with_doc(project());self.packet=evidence_packet(self.p);self.value=design_result(self.packet)
        eid=self.packet['datasheet_excerpts'][0]['evidence_id']
        self.value['circuit_findings']=[{'reference':'U42','title':'Check the charger ground','check_type':'supply',
            'requirement':'The exposed pad needs ground.','reason':'Review the connection.','use_case':'Charging the battery.',
            'missing_context':['Confirm the package.'],'observations':[{'reference':'U42','pin':'6','net':'GND'}],
            'steps':['Find pin 6.','Check the pad connection.'],'evidence_ids':['C:U42',eid],
            'citations':[{'evidence_id':eid,'quote':'The exposed pad must be connected to VSS.'}]}]
    def validate(self):return validate_review(self.value,allowed_evidence(self.packet),schema=DESIGN_SCHEMA,packet=self.packet)
    def test_supported_new_finding_joins_plan_with_source_and_steps(self):
        self.p.ai_review={'status':'completed','result':self.validate()}
        plan=compose_plan(self.p)
        self.assertEqual(plan['actions'][-1]['basis'],'AI schematic review')
        self.assertTrue(plan['actions'][-1]['citations'])
        self.assertEqual(self.p.review['groups'][-1]['priority'],'review')
        count=len(self.p.review['groups']);compose_plan(self.p);self.assertEqual(len(self.p.review['groups']),count)
    def test_invented_pin_net_or_quote_is_rejected(self):
        original=copy.deepcopy(self.value)
        for kind in ('pin','net','quote','missing_citation'):
            self.value=copy.deepcopy(original);f=self.value['circuit_findings'][0]
            if kind in ('pin','net'):f['observations'][0][kind]='invented'
            if kind=='quote':f['citations'][0]['quote']='A rule that is absent from this PDF.'
            if kind=='missing_citation':f['citations']=[]
            with self.subTest(kind=kind),self.assertRaises(AIError):self.validate()
    def test_ai_omission_does_not_check_off_previous_concern(self):
        self.p.ai_review={'status':'completed','result':self.validate()};compose_plan(self.p)
        before=asdict(self.p);after=copy.deepcopy(before)
        after['review']['groups']=[g for g in after['review']['groups'] if g.get('basis')!='AI schematic review']
        after['provenance']['source_hashes']={'pcb':'new'}
        progress=compare_runs(before,after)['progress']
        self.assertEqual(next(i['status'] for i in progress['items'] if i['title']=='Check the charger ground'),'unverified')
