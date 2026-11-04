"""Naming notices must explain the saved nets without inventing wiring work."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.models import Project, Issue, Pin, Pad
from pcb_checker.evidence_analysis import erc_findings
from pcb_checker.review import build_review
from pcb_checker.design_review import local_plan
from pcb_checker.ai.reviewer import DESIGN_SCHEMA, AIError, allowed_evidence, evidence_packet, focused_packet, validate_review
from test_review import component
from test_pin_analysis import example, answer
from pcb_checker.step_help import answer_step


def naming_project():
    p=Project('names', connectivity={'status':'compared'})
    p.components['U11']=component('U11','MAX30102',[Pin('10','VLED+','power_in','+3.3V')],[Pad('10','+3.3V',1)])
    p.schematic_nets={'+3.3V':['U11:10']}
    p.issues=[Issue('warning','KICAD_ERC_MULTIPLE_NET_NAMES',
        'Both +3.3V and VLED+ are attached to the same items; +3.3V will be used in the netlist',
        source='kicad_erc',items=[{'uuid':'hidden','description':'Symbol U11 Hidden pin 10 [VLED+, Power input, Line]'}])]
    return p


class NetAliasTests(unittest.TestCase):
    def test_hidden_pin_and_real_board_assignment_are_included(self):
        p=naming_project();p.review=build_review(p);g=p.review['groups'][0]
        pin=erc_findings(p,g)[0]['connections'][0]
        self.assertEqual((pin['reference'],pin['pin'],pin['net']),('U11','10','+3.3V'))
        alias=g['net_aliases'][0]
        self.assertEqual(alias['names'],['+3.3V','VLED+'])
        self.assertTrue(alias['pins'][0]['board_matches'])
        self.assertIn('hidden power pin',alias['explanation'])
        action=local_plan(p)['actions'][0]
        self.assertEqual(action['disposition'],'no_change')
        self.assertEqual(action['steps'],[])
        self.assertEqual(g['priority'],'note')
        self.assertEqual(p.issues[0].severity,'warning')

    def test_chat_explains_a_notice_without_manufacturing_fix_steps(self):
        p=naming_project();p.review=build_review(p)
        result=answer_step(p,p.review['groups'][0],local_plan(p)['actions'][0],0,'Does this need a change?',[],key='')
        self.assertEqual(result['status'],'local')
        self.assertIn('no required edit',result['answer'])
        self.assertIn('VLED+',result['answer'])
        self.assertNotIn('read a datasheet',result['answer'])

    def test_arbitrary_aliases_keep_the_exact_intent_question(self):
        p=naming_project();p.issues[0].items=[]
        p.issues[0].message='Both +Bat and INVB are attached to the same items; +Bat will be used in the netlist'
        p.review=build_review(p);alias=p.review['groups'][0]['net_aliases'][0]
        self.assertEqual(alias['chosen_net'],'+Bat')
        self.assertIn('separate connections',alias['intent_question'])
        self.assertNotIn('intentional',alias['explanation'])

    def test_board_mismatch_is_not_reported_as_a_match(self):
        p=naming_project();p.components['U11'].pcb[0].pads[0].net='GND'
        p.review=build_review(p)
        self.assertFalse(p.review['groups'][0]['net_aliases'][0]['pins'][0]['board_matches'])

    def test_unrecognized_native_message_stays_for_review(self):
        p=naming_project();p.issues[0].message='Unrecognized alias format'
        g=build_review(p)['groups'][0]
        self.assertEqual(g['priority'],'review')
        self.assertNotIn('no_change_basis',g)

    def test_proven_pin_connection_can_have_no_required_steps(self):
        packet=evidence_packet(example());value=answer(packet)
        value['actions'][0].update(disposition='no_change',steps=[])
        validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        value['actions'][0]['pin_checks'][0]['decision']='needs_context'
        value['actions'][0]['pin_checks'][0]['missing_context']=['Will firmware use I2C?']
        with self.assertRaises(AIError):validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_no_change_cannot_dismiss_a_fabrication_limit(self):
        packet=evidence_packet(example());value=answer(packet)
        value['actions'][0].update(disposition='no_change',steps=[],pin_checks=[])
        group=packet['review_groups'][0];group.update(category='manufacturing',requires_pin_analysis=False)
        with self.assertRaises(AIError):validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_focused_retry_preserves_targets_and_removes_unrelated_parts(self):
        packet=evidence_packet(example());packet['components'].append({'reference':'U999','evidence_id':'C:U999','pins':[]})
        focused=focused_packet(packet,packet['action_group_ids'])
        self.assertNotIn('U999',[c['reference'] for c in focused['components']])
        original=packet['review_groups'][0]['pin_targets']
        self.assertEqual(focused['review_groups'][0]['pin_targets'],[{k:v for k,v in t.items() if k!='net_members'} for t in original])
        for target in original:
            net=next(n for n in focused['nets'] if n['name']==target['net'])
            self.assertEqual(net['pins'],example().schematic_nets[target['net']])
        self.assertEqual(focused['action_group_ids'],packet['action_group_ids'])

if __name__=='__main__':unittest.main()
