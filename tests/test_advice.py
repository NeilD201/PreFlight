"""Recommendations use evidence and never require busywork to fill a checklist."""
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.advice import apply_advice
from pcb_checker.ai.reviewer import AIError, DESIGN_SCHEMA, allowed_evidence, evidence_packet, validate_review
from pcb_checker.design_review import current_plan, local_plan
from pcb_checker.datasheets import excerpts
from test_pin_analysis import example, answer, QUOTE
from test_manufacturing import measured, check

class AdviceTests(unittest.TestCase):
    def test_one_missing_fact_does_not_need_a_second_filler_step(self):
        packet=evidence_packet(example());value=answer(packet)
        value['actions'][0]['steps']=['Will firmware use this pin as an input?']
        value['actions'][0]['pin_checks'][0].update(decision='needs_context',missing_context=['Will firmware use this pin as an input?'])
        validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_conditional_keep_can_have_no_steps_but_citations_still_required(self):
        p=example();p.datasheets['parts'][0]['status']='provisional'
        packet=evidence_packet(p);value=answer(packet);value['actions'][0]['steps']=[]
        validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        value['actions'][0]['pin_checks'][0]['citations']=[]
        with self.assertRaises(AIError):validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_pin_review_is_not_dropped_when_classified_as_documentation(self):
        p=example();p.review['groups'][0]['category']='documentation'
        packet=evidence_packet(p)
        self.assertEqual(packet['action_group_ids'],[p.review['groups'][0]['id']])

    def test_table_window_starts_at_table_and_keeps_exact_source_text(self):
        p=example();text='Irrelevant text. '*112+'\nPin descriptions\n1 SA0: '+QUOTE+'\n'
        p.datasheets['documents']['C150772']['pages']=[{'page':1,'text':text}]
        parts=excerpts(p)
        self.assertTrue(any(d['text'].startswith('Pin descriptions') and QUOTE in d['text'] for d in parts))
        for d in parts:self.assertIn(d['text'],text)

    def test_repeated_via_options_become_one_order_action(self):
        p=example();p.issues[0].code='KICAD_DRC_VIA_DIAMETER';p.issues[0].items=[{'description':'Via [GND] on F.Cu - B.Cu'}]
        m=measured();m['vias'].append({**m['vias'][0],'count':3,'drill_mm':.2,'diameter_mm':.3})
        p.manufacturing=check(data=asdict(p),m=m)
        a=current_plan(p)['actions'][0]
        self.assertEqual(len(a['steps']),1)
        self.assertIn('4 small vias',a['advice_title'])
        self.assertIn('cost',a['advice_summary'])

    def test_missing_context_becomes_specific_question_without_altering_history(self):
        p=example();packet=evidence_packet(p);action=answer(packet)['actions'][0]
        action['pin_checks'][0].update(decision='needs_context',missing_context=['Is this pin used to start measurements?'])
        action.update(basis='AI guidance from supplied evidence')
        plan={'actions':[action]};saved=deepcopy(plan)
        revised=apply_advice(plan,asdict(p))
        self.assertEqual(revised['actions'][0]['steps'],['Is this pin used to start measurements?'])
        self.assertEqual(plan,saved)

    def test_library_difference_never_proposes_blind_replacement(self):
        p=example();p.issues[0].code='KICAD_ERC_LIB_SYMBOL_MISMATCH'
        action={'group_id':p.review['groups'][0]['id'],'steps':['Update symbols from library.'],'basis':'AI guidance from supplied evidence','reason':'Copy differs.'}
        revised=apply_advice({'actions':[action]},asdict(p))['actions'][0]
        self.assertEqual(revised['steps'],[])
        self.assertIn('could change',revised['reason'])

    def test_unknown_antenna_is_not_a_proven_failure(self):
        packet=evidence_packet(example());value=answer(packet)
        packet['review_groups'][0]['signal_path_id']='rf-U42'
        value['actions'][0]['reason']='This will prevent wireless transmission.'
        with self.assertRaises(AIError):validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_rf_display_replaces_stale_unsupported_failure_claim(self):
        p=asdict(example());g=p['review']['groups'][0];g['signal_path_id']='rf-U42'
        p['signal_paths']={'paths':[{'id':'rf-U42','anchor':'U42','series_component':'L1','nets':[{'net':'RF'},{'net':'END'}]}]}
        action={'group_id':g['id'],'basis':'AI guidance from supplied evidence','reason':'This will prevent wireless transmission.'}
        a=apply_advice({'actions':[action]},p)['actions'][0]
        self.assertNotIn('will prevent',a['reason'])
        self.assertIn('not establish',a['reason'])
        self.assertEqual(len(a['steps']),1)

    def test_unknown_antenna_is_analyzed_even_without_design_context(self):
        p=example();g=p.review['groups'][0];g.update(signal_path_id='rf-U42',category='signal')
        p.issues[0].code='RF_FEED_NO_TERMINAL'
        packet=evidence_packet(p)
        self.assertEqual(packet['action_group_ids'],[g['id']])
        self.assertIn('Trace the supplied RF path',packet['review_groups'][0]['decision_constraint'])
        self.assertTrue(packet['components'])
        p.design_context['operating_context']={'radio':'An external antenna is planned after L1.'}
        self.assertEqual(evidence_packet(p)['action_group_ids'],[g['id']])

    def test_conditional_antenna_assessment_survives_without_radio_context(self):
        p=asdict(example());g=p['review']['groups'][0];g['signal_path_id']='rf-U42'
        p['signal_paths']={'paths':[{'id':'rf-U42','anchor':'U42','series_component':'L1','nets':[{'net':'RF'},{'net':'END'}]}]}
        action={'group_id':g['id'],'basis':'AI guidance from supplied evidence','disposition':'investigate',
                'reason':'No endpoint was identified; an external connection could exist.',
                'steps':['A connector is one candidate approach, pending its geometry and package requirements.']}
        revised=apply_advice({'actions':[action]},p)['actions'][0]
        self.assertEqual(revised['steps'],action['steps'])

    def test_reported_antenna_cannot_be_presented_as_a_verified_connection(self):
        p=asdict(example());g=p['review']['groups'][0];g['signal_path_id']='rf-U42'
        p['signal_paths']={'paths':[{'id':'rf-U42','anchor':'U42','series_component':'L7','terminal':None,'nets':[{'net':'RF'},{'net':'END'}]}]}
        action={'group_id':g['id'],'basis':'AI guidance from supplied evidence','disposition':'investigate',
                'title':'RF feed reaches reported antenna','reason':'The antenna is connected based on your answer.',
                'steps':['Compare the intended antenna with the saved RF output.']}
        revised=apply_advice({'actions':[action]},p)['actions'][0]
        self.assertIn('no antenna connection identified',revised['advice_title'])
        self.assertNotIn('antenna is connected',revised['reason'])
        self.assertEqual(revised['disposition'],'investigate')
        self.assertIn('no identified antenna endpoint',revised['consequence']['text'])

    def test_local_plan_after_context_change_keeps_antenna_evidence(self):
        p=example();g=p.review['groups'][0];g['signal_path_id']='rf-U42'
        p.signal_paths={'paths':[{'id':'rf-U42','anchor':'U42','series_component':'L7','terminal':None,'nets':[{'net':'RF'},{'net':'END'}]}]}
        p.design_context['revision']=34
        action=local_plan(p)['actions'][0]
        self.assertIn('no antenna connection identified',action['advice_title'])
        self.assertEqual(action['disposition'],'investigate')

if __name__=='__main__':unittest.main()
