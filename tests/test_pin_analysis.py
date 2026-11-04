"""ERC analysis must resolve the actual pins and cite each part's own requirements."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import vague_routing_steps, AIError, DESIGN_SCHEMA, allowed_evidence, evidence_packet, validate_review
from pcb_checker.datasheets import excerpts
from pcb_checker.evidence_analysis import erc_findings
from pcb_checker.models import Issue, Pin
from test_design_review import project, with_doc, design_result

QUOTE='The SA0 pin can connect to ground to select the low address.'


def example():
    p=with_doc(project())
    p.components['U42'].schematic_pins=[Pin('1','SA0','bidirectional','GND')]
    p.issues=[Issue('warning','KICAD_ERC_PIN_TO_PIN','Pin types conflict',source='kicad_erc',items=[
        {'description':'Symbol U42 Pin 1 [SA0, Bidirectional, Line]','uuid':'actual-pin'},
        {'description':'Symbol #FLG01 Pin 1 [Power output, Line]','uuid':'virtual-pin'}])]
    p.review['groups']=[{**p.review['groups'][0],'issue_ids':['F0001'],'components':['U42']}]
    doc=p.datasheets['documents']['C150772'];doc['pages']=[{'page':1,'text':QUOTE}]
    p.schematic_nets={'GND':['U42:1','J1:2']}
    return p


def answer(packet):
    value=design_result(packet);action=value['actions'][0]
    action['citations']=[];action['reason']='The ground strap selects the low address.'
    action['steps']=['Keep U42 pin 1 connected to GND.','Use the low address when talking to this part.']
    action['pin_checks']=[{'reference':'U42','pin':'1','net':'GND','decision':'keep',
        'explanation':'No wiring change needed. The ground connection selects the low address.',
        'missing_context':[],'citations':[{'evidence_id':packet['datasheet_excerpts'][0]['evidence_id'],'quote':QUOTE}]}]
    return value


class PinAnalysisTests(unittest.TestCase):
    def test_supply_limits_survive_many_high_scoring_mode_pages(self):
        p=example();p.components['U42'].schematic_pins.append(Pin('8','VDD','bidirectional','+3.3V'))
        p.issues[0].items.append({'description':'Symbol U42 Pin 8 [VDD, Bidirectional, Line]','uuid':'supply'})
        p.datasheets['documents']['C150772']['pages']=[
            {'page':i,'text':('SA0 mode selection connected to ground address select ' * 20)+str(i)} for i in range(1,9)] + [
            {'page':12,'text':'Electrical characteristics\nSymbol Min. Typ. Max. Unit\nVdd Supply voltage 1.71 1.8 3.6 V'}]
        passages=excerpts(p,include_audit=False)
        self.assertTrue(any('1.71 1.8 3.6' in d['text'] for d in passages))
        self.assertLessEqual(len(passages),6)
    def test_generic_routing_tasks_are_rejected_but_specific_context_is_allowed(self):
        self.assertTrue(vague_routing_steps(['Check return paths and nearby switching noise sources.']))
        self.assertTrue(vague_routing_steps(['Inspect ECGP and ECGN routing.']))
        self.assertFalse(vague_routing_steps(['Tell the app which antenna attaches to Net-(C33-Pad1).']))
        self.assertFalse(vague_routing_steps(['Move U4 output pin 5 off GND and reconnect it to the intended output rail.']))
    def test_technical_verification_steps_are_rejected_not_recommended(self):
        from pcb_checker.ai.reviewer import delegated_analysis
        self.assertTrue(delegated_analysis(['Confirm SW9 mounting tabs are mechanical only - no wire change yet.']))
        self.assertTrue(delegated_analysis(['Verify the U1 footprint pad numbers match the datasheet land pattern.']))
        self.assertTrue(delegated_analysis(['Check whether the J3 shield terminals connect to ground.']))
        self.assertTrue(delegated_analysis(['Confirm the part identity before interpreting its requirements.']))
        self.assertFalse(delegated_analysis(['Tell the app which antenna attaches to Net-(C33-Pad1).']))
        self.assertFalse(delegated_analysis(['Move U4 output pin 5 off GND and reconnect it to the intended output rail.']))

    def test_erc_uuid_is_bound_to_exported_pin_net_and_members(self):
        p=example();packet=evidence_packet(p);group=packet['review_groups'][0]
        self.assertTrue(group['requires_pin_analysis'])
        self.assertEqual([(t['reference'],t['pin'],t['net']) for t in group['pin_targets']],[('U42','1','GND')])
        actual=group['erc_findings'][0]['connections'][0]
        self.assertEqual(actual['uuid'],'actual-pin')
        members=next(net['pins'] for net in packet['nets'] if net['name']==actual['net'])
        self.assertIn('J1:2',members)
        self.assertTrue(group['erc_findings'][0]['connections'][1]['virtual'])

    def test_conflicting_multiunit_pin_nets_are_not_guessed(self):
        p=example();p.components['U42'].schematic_pins.append(Pin('1','SA0','bidirectional','OTHER'))
        self.assertIsNone(erc_findings(p,p.review['groups'][0])[0]['connections'][0]['net'])

    def test_pin_passages_beat_unrelated_keyword_heavy_pages(self):
        p=example();doc=p.datasheets['documents']['C150772']
        doc['pages']=[{'page':n,'text':' '.join(['recommended supply layout capacitor operating']*100)} for n in range(1,10)]
        doc['pages'].append({'page':10,'text':QUOTE})
        self.assertIn(10,[d['page'] for d in excerpts(p)])

    def test_citation_feedback_names_the_pin_and_matching_excerpt(self):
        packet=evidence_packet(example());value=answer(packet)
        value['actions'][0]['pin_checks'][0]['citations'][0]['quote']='Invented quotation about this pin.'
        with self.assertRaises(AIError) as caught:
            validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        message=str(caught.exception)
        self.assertIn('U42 pin 1 on GND',message)
        self.assertIn('not verbatim',message)
        self.assertIn(packet['datasheet_excerpts'][0]['evidence_id'],message)

    def test_verified_pin_decision_is_accepted(self):
        packet=evidence_packet(example())
        validate_review(answer(packet),allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_missing_pin_wrong_net_wrong_datasheet_and_generic_tasks_are_rejected(self):
        packet=evidence_packet(example());good=answer(packet)
        for failure in ('missing','wrong_net','no_proof','other_part','generic_steps','table_title','generic_question'):
            bad=copy.deepcopy(good);action=bad['actions'][0];check=action['pin_checks'][0]
            changed=copy.deepcopy(packet)
            if failure=='missing':action['pin_checks']=[]
            if failure=='wrong_net':check['net']='VDD'
            if failure=='no_proof':check['citations']=[]
            if failure=='other_part':changed['datasheet_excerpts'][0]['references']=['U99']
            if failure=='generic_steps':action['steps'][0]='Inspect the datasheet configuration requirements for address strapping.'
            if failure=='table_title':
                check['citations'][0]['quote']='Table 19. Internal pin status'
                changed['datasheet_excerpts'][0]['text']='Table 19. Internal pin status'
            if failure=='generic_question':check['missing_context']=['Verify the configuration requirements in the datasheet.']
            with self.subTest(failure=failure),self.assertRaises(AIError):
                validate_review(bad,allowed_evidence(changed),schema=DESIGN_SCHEMA,packet=changed)

    def test_user_directed_verification_steps_rejected_without_pin_analysis(self):
        import copy
        p=example();packet=evidence_packet(p)
        changed=copy.deepcopy(packet)
        changed['review_groups'][0]['requires_pin_analysis']=False
        bad=answer(changed)
        bad['actions'][0]['pin_checks']=[]
        bad['actions'][0]['steps']=['Confirm SW9 mounting tabs are mechanical only - no wire change yet.']
        with self.assertRaises(AIError) as caught:
            validate_review(bad,allowed_evidence(changed),schema=DESIGN_SCHEMA,packet=changed)
        self.assertIn('supplied technical analysis',str(caught.exception))
        good=answer(changed)
        good['actions'][0]['pin_checks']=[]
        good['actions'][0]['steps']=['Keep U42 pin 1 connected to GND.']
        validate_review(good,allowed_evidence(changed),schema=DESIGN_SCHEMA,packet=changed)

    def test_unavailable_intent_is_specific_and_does_not_fake_a_requirement(self):
        packet=evidence_packet(example());value=answer(packet);check=value['actions'][0]['pin_checks'][0]
        check.update(decision='needs_context',citations=[],missing_context=['Will firmware use this pin as an external trigger?'])
        validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)


if __name__=='__main__':unittest.main()
