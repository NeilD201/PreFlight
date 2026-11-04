import sys,unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.user_profile import guided_questions,PROFILE,blocks_review,check_flow_questions
from pcb_checker.product_context import unanswered_followups,product_evidence
from pcb_checker.review_inputs import review_inputs
from pcb_checker.verification import evaluate,edit
from test_design_review import project
from test_verification import design

class ProfileTests(unittest.TestCase):
    def test_experience_is_first_and_only_hardware_questions_follow(self):
        p=project();first=guided_questions(p)
        self.assertEqual(first['unanswered'],['experience'])
        answers={'experience':'Beginner — ask about the product'}
        result=guided_questions(p,answers)
        # No generic product checklist: ordinary reviews proceed with zero
        # pending questions. PROFILE answers stay saved and editable.
        self.assertEqual(result['questions'],[])
        self.assertEqual(result['unanswered'],[])
        self.assertEqual({q['id'] for q in result['all_questions']},
                         {q['id'] for q in first['all_questions']})
        self.assertTrue({'purpose','audience','charger','enclosure','behavior'} <=
                        {q['id'] for q in result['all_questions']})
        self.assertFalse(any('capacitance' in q['question'].lower() or 'GPIO' in q['question'] for q in result['questions']))
    def test_expert_keeps_precise_component_questions(self):
        result=guided_questions(project(),{'experience':'Experienced EE — show electrical limits'})
        self.assertTrue(any(q['id'].startswith('battery:') for q in result['questions']))
    def test_prior_case_description_is_reused_across_file_changes(self):
        p=project();context={'product_context':{'experience':'Beginner — ask about the product'},
            'question_answers':{'a':{'question':'What enclosure and nearby materials surround the antenna, and what range do you need?', 'answer':'Plastic case, on the wrist, 10 m range.'}}}
        first=guided_questions(p,context=context)
        self.assertEqual(first['answers']['enclosure'],'Plastic case, on the wrist, 10 m range.')
        self.assertNotIn('enclosure',first['unanswered'])
        p.provenance['source_hashes']={'pcb':'different'}
        self.assertNotIn('enclosure',guided_questions(p,context=context)['unanswered'])
        self.assertEqual(unanswered_followups(['What enclosure will surround this device?'],context),[])
        self.assertEqual(len(unanswered_followups(['What maximum temperature was measured inside the enclosure?'],context)),1)
        self.assertEqual(len(unanswered_followups(['Which antenna connects to L1?'],context)),1)
    def test_review_answers_bridge_matching_profile_facts(self):
        p=project();context={'product_context':{'experience':'Beginner — ask about the product'},
            'question_answers':{
                'a':{'question':'What is this product meant to do?','answer':'A bike light.'},
                'b':{'question':'Which battery pack will connect to J4, and can the product run while charging?','answer':'USB-C 5V.'},
                'c':{'question':'Will the product be used outdoors, near water, or where condensation can form?','answer':'Indoor only.'},
                'd':{'question':'What external device or supply connects to J1?','answer':'A USB cable.'}}}
        result=guided_questions(p,context=context)
        self.assertEqual(result['answers']['purpose'],'A bike light.')
        self.assertEqual(result['answers']['charger'],'USB-C 5V.')
        self.assertEqual(result['answers']['environment'],'Indoor only.')
        self.assertEqual(result['answers']['connections'],'A USB cable.')
        self.assertFalse({'purpose','charger','environment','connections'} & set(result['unanswered']))
    def test_presentation_preferences_never_block_analysis(self):
        p=project()
        first=guided_questions(p)
        # The experience question is still offered in the UI…
        self.assertEqual(first['unanswered'],['experience'])
        # …but it must never gate the Vector review on its own.
        self.assertEqual(first['blocking_unanswered'],[])
        answered=guided_questions(p,{'experience':'Beginner — ask about the product'})
        self.assertEqual(answered['blocking_unanswered'],[])
    def test_firmware_behavior_questions_never_block_analysis(self):
        profile_ids={key for key,_,_,_ in PROFILE}
        for qid in ['behavior:SW1:0123456789','behavior:D100:0123456789',
                    'power:board:0123456789','experience','purpose']:
            self.assertFalse(blocks_review(qid,profile_ids),qid)
        for qid in ['battery:U1:0123456789','radio:U2:0123456789',
                    'loads:M1:0123456789','connector:J1:0123456789',
                    'mode:U3:0123456789','environment:U4:0123456789',
                    'body:D5:0123456789']:
            self.assertTrue(blocks_review(qid,profile_ids),qid)
    def test_check_flow_hides_firmware_behavior_questions(self):
        def q(qid):
            return {'id':qid,'question':qid,'reference':'U1','known':'','hint':''}
        result={'questions':[q('experience'),q('behavior:SW1:0123456789'),
                             q('power:board:0123456789'),q('battery:U1:0123456789')],
                'unanswered':['experience','behavior:SW1:0123456789',
                              'power:board:0123456789','battery:U1:0123456789'],
                'answers':{},'all_questions':[],'answered_questions':[]}
        filtered=check_flow_questions(result)
        self.assertEqual([q['id'] for q in filtered['questions']],['experience','battery:U1:0123456789'])
        self.assertEqual(filtered['unanswered'],['experience','battery:U1:0123456789'])
        # Saved answers and the full inventory are untouched.
        self.assertEqual(filtered['answers'],{})
        self.assertEqual(filtered['all_questions'],[])
    def test_hardware_questions_still_block_analysis(self):
        result=guided_questions(project(),{'experience':'Experienced EE — show electrical limits'})
        battery=[q['id'] for q in result['questions'] if q['id'].startswith('battery:')]
        self.assertTrue(battery,'fixture should surface a hardware-driven question')
        for bid in battery:
            if bid in result['unanswered']:
                self.assertIn(bid,result['blocking_unanswered'])
    def test_experience_only_keeps_review_gate_open(self):
        from dataclasses import asdict
        from pcb_checker.release_review import before_ordering
        p=project()
        # Mirrors the analysis gate in webapp start(): only hardware-driven
        # questions and review-input tasks may hold back the Vector review.
        input_design=asdict(p)
        input_design['verification_results']=evaluate(input_design)
        input_design['before_ordering']=before_ordering(input_design)
        awaiting=bool(guided_questions(p)['blocking_unanswered'] or review_inputs(input_design)['tasks'])
        self.assertFalse(awaiting)
    def test_offtopic_mentions_do_not_fill_profile(self):
        from pcb_checker.user_profile import topics
        safety='Will electrodes remain connected to a person while either the charger or debugger is plugged in?'
        probe='When a debugger is plugged into J5, will it supply power or only sense the voltage?'
        self.assertEqual(topics(safety),set())
        self.assertEqual(topics(probe),set())
        self.assertEqual(topics('Tell us about your electronics experience.'),set())
        p=project();context={'product_context':{'experience':'Beginner — ask about the product'},
            'question_answers':{'a':{'question':safety,'answer':'Yes'},'b':{'question':probe,'answer':'Sense only.'}}}
        result=guided_questions(p,context=context)
        self.assertFalse({'charger','connections'} & set(result['answers']))
        self.assertFalse({'charger','connections'} & set(result['unanswered']))
    def test_beginner_never_receives_engineering_forms_but_checks_stay_unresolved(self):
        data=design();data['design_context']={'product_context':{k:'Not sure yet' for k,*_ in PROFILE}}
        data['verification_results']=evaluate(data)
        result=review_inputs(data)
        self.assertFalse(result['needs_product_profile']);self.assertEqual(result['tasks'],[])
        self.assertGreater(result['technical_inputs_unresolved'],0)
        self.assertTrue(all(c['status']=='missing' for c in data['verification_results']['calculations']))
        data['design_context']['product_context']['experience']='Experienced EE — show electrical limits'
        self.assertTrue(any(t['kind']=='calculation' for t in review_inputs(data)['tasks']))
    def test_experience_and_prior_pin_answers_reach_ai_evidence(self):
        p=project();context={'product_context':{'experience':'Beginner — ask about the product','enclosure':'Plastic'},'question_answers':{'a':{'question':'What battery?', 'answer':'Not sure yet'}}}
        evidence=product_evidence(context,p)
        self.assertEqual(evidence['user_profile']['enclosure'],'Plastic')
        self.assertTrue(evidence['saved_followup_answers'])

class SuggestionTests(unittest.TestCase):
    def data(self):
        d=design();bus=d['circuit']['bus_candidates'][0];bus['parallel_pullups'][0]['rail']='+3.3V';bus['pullups'][0]['reference']='R1'
        d['components']['R1']={'schematic':[{'value':'1.5k','properties':{'Tolerance':'±1%'}}],'schematic_pins':[]}
        return d
    def test_prefill_has_sources_and_does_not_invent_other_limits(self):
        c=next(c for c in evaluate(self.data())['calculations'] if c['kind']=='i2c')
        self.assertEqual(c['suggested_values']['voltage']['value'],3.3)
        self.assertEqual(c['suggested_values']['tolerance']['value'],1)
        self.assertEqual(set(c['suggested_values']),{'voltage','tolerance'})
        self.assertEqual(c['status'],'missing');self.assertEqual(c['values'],{})
        self.assertIn('Nominal',c['suggested_values']['voltage']['qualification'])
    def test_no_tolerance_guessed_when_properties_are_missing(self):
        d=self.data();d['components']['R1']['schematic'][0]['properties']={}
        c=next(c for c in evaluate(d)['calculations'] if c['kind']=='i2c')
        self.assertNotIn('tolerance',c['suggested_values'])
    def test_suggestion_requires_confirmation_before_calculating(self):
        d=self.data();c=next(c for c in evaluate(d)['calculations'] if c['kind']=='i2c')
        req={'target':c['key'],'signature':c['signature'],'note':'Confirmed operating limits','values':{'voltage':3.3,'tolerance':1,'capacitance':100,'rise':300,'sink':3,'low':.4}}
        with self.assertRaisesRegex(ValueError,'Confirm'):edit(d,req,{})
        result=edit(d,{**req,'confirm_suggestions':True},{})
        self.assertIn('voltage',result['entries'][c['key']]['confirmed_suggestions'])
