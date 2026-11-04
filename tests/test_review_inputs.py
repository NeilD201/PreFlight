import copy,sys,unittest
from dataclasses import asdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.review_inputs import review_inputs
from pcb_checker.storage import WorkspaceStore
from pcb_checker.release_review import before_ordering
from pcb_checker.verification import evaluate
from pcb_checker.design_review import context_key
from pcb_checker.webapp import AppError
from test_verification import design
from test_design_review import project
import test_webapp as web_tests

class InputTests(unittest.TestCase):
    def data(self):
        data=design();data['design_context']={'product_context':{'experience':'Experienced EE — show electrical limits'}};data['verification_results']=evaluate(data);data['before_ordering']=before_ordering(data)
        return data
    def test_queue_contains_actual_missing_limits_and_physical_questions(self):
        tasks=review_inputs(self.data())['tasks']
        self.assertTrue(any(t['kind']=='calculation' and any(key.startswith('i2c:') for key in t['targets']) for t in tasks))
        self.assertEqual(len([t for t in tasks if t['kind']=='physical']),4)
        self.assertTrue(all(t['signature'] and t['question'] for t in tasks))
    def test_calculations_share_topics_without_sharing_their_values(self):
        data=self.data();first=data['verification_results']['calculations'][0]
        duplicate=copy.deepcopy(first);duplicate['key']+='other';duplicate['signature']+='other'
        data['verification_results']['calculations'].append(duplicate)
        task=next(t for t in review_inputs(data)['tasks'] if t.get('section')==first['section'])
        self.assertIn(first['key'],task['targets']);self.assertIn(duplicate['key'],task['targets'])
        signature=task['signature'];first['status']='checked'
        updated=next(t for t in review_inputs(data)['tasks'] if t.get('section')==first['section'])
        self.assertEqual(updated['signature'],signature)
        self.assertNotIn(first['key'],updated['targets']);self.assertIn(duplicate['key'],updated['targets'])

    def test_saved_unknown_is_not_reasked_or_counted_as_passed(self):
        data=self.data();task=next(t for t in review_inputs(data)['tasks'] if t['id']=='physical:mechanical')
        data['design_context']={**data['design_context'],'question_answers':{'a':{'question':task['question'],'answer':'Not sure yet','review_task':{k:task[k] for k in ('id','signature')}}}}
        self.assertNotIn(task['id'],[t['id'] for t in review_inputs(data)['tasks']])
        self.assertEqual(next(r for r in before_ordering(data)['checks'] if r['id']=='mechanical')['status'],'missing')
        data['provenance']['source_hashes']['board.kicad_pcb']='changed'
        self.assertIn(task['id'],[t['id'] for t in review_inputs(data)['tasks']])
    def test_saved_unknown_calc_survives_later_context_answers(self):
        # MACRO-EXP Expert loop: saving any interview answer rewrites
        # design_context, which used to rotate every calculation signature
        # and re-ask "I don't know" topics forever.
        data=self.data()
        data['components']['J1']={'schematic':[{'value':'CONN'}],'pcb':[],
            'schematic_pins':[{'number':'1','name':'SIG','net':'NET1','electrical_type':'passive'}]}
        data['verification_results']=evaluate(data)
        task=next(t for t in review_inputs(data)['tasks'] if t['id']=='calculation:esd_margin')
        data['design_context']={**data['design_context'],
            'question_answers':{'a':{'question':task['question'],'answer':"I don't know",
                'review_task':{'id':task['id'],'signature':task['signature']}}},
            'product_context':{**data['design_context']['product_context'],'purpose':'macro keypad'}}
        data['verification_results']=evaluate(data)
        self.assertNotIn('calculation:esd_margin',[t['id'] for t in review_inputs(data)['tasks']])
    def test_ai_questions_are_direct_and_saved_answers_suppress_repeats(self):
        data=self.data();data['saved_review_questions']=['What battery will you use?','No further questions.']
        data['design_context']={**data['design_context'],'question_answers':{'a':{'question':'What battery will you use?','answer':'Not sure yet'}}}
        self.assertFalse(any(t['kind']=='question' for t in review_inputs(data)['tasks']))
    def test_completed_or_failed_calculations_do_not_ask_for_missing_values(self):
        data=self.data()
        for c in data['verification_results']['calculations']:c['status']='attention'
        self.assertFalse(any(t['kind']=='calculation' for t in review_inputs(data)['tasks']))

    def test_pin_question_reaches_interview_without_top_level_duplicate(self):
        data=self.data();question='Should U1 keep running while the charger connected at J4 charges the battery?'
        generic='Should the product work while it is charging?'
        data['ai_review']={'status':'completed','result':{'questions':[]}}
        data['action_plan']={'actions':[{'pin_checks':[{'decision':'needs_context','missing_context':[question,question,generic,'Can you inspect the datasheet pin table?']}]}]}
        out=review_inputs(data)
        tasks=[t for t in out['tasks'] if t['kind']=='question']
        self.assertEqual([t['question'] for t in tasks],[question])
        self.assertTrue(any(generic in u['need'] for u in out['unknowns']))
        data['design_context']['question_answers']={'saved':{'question':question,'answer':'Yes'}}
        self.assertFalse(any(t['kind']=='question' for t in review_inputs(data)['tasks']))
        data['design_context']['question_answers']={};data['ai_review']['status']='stale'
        self.assertFalse(any(t['kind']=='question' for t in review_inputs(data)['tasks']))

    def test_only_anchored_questions_reach_the_interview(self):
        data=self.data();data['design_context']['product_context']['experience']='I can solder and use KiCad'
        technical='What I²C speed will the firmware use, and what is the highest actual +3.3V rail voltage including its supply tolerance?'
        generic='Will electrodes remain connected to a person while either the charger or debugger is plugged in?'
        anchored='Will electrodes on J3 remain connected to a person while the charger at J4 is plugged in?'
        data['saved_review_questions']=[technical,generic,anchored]
        out=review_inputs(data)
        self.assertEqual([t['question'] for t in out['tasks'] if t['kind']=='question'],[anchored])
        self.assertTrue(any(generic in u['need'] for u in out['unknowns']))
        self.assertTrue(any('I²C speed' in d['need'] for d in out['delegations']))
        data['design_context']['product_context']['experience']='Experienced EE — show electrical limits'
        out=review_inputs(data)
        self.assertEqual([t['question'] for t in out['tasks'] if t['kind']=='question'],[anchored])

class SaveTests(unittest.TestCase):
    setUp=web_tests.StateTests.setUp
    tearDown=web_tests.StateTests.tearDown
    def prepare(self):
        self.state.project=project();self.state.report_id='review'
        self.state.project.design_context['product_context']={'experience':'Experienced EE — show electrical limits'}
        data=asdict(self.state.project);data['verification_results']=evaluate(data);data['before_ordering']=before_ordering(data)
        return next(t for t in review_inputs(data)['tasks'] if t['kind']=='physical')
    def test_answer_survives_restart_and_remains_an_unverified_user_report(self):
        task=self.prepare();p=self.state.project
        result=self.state.save_question_answer({'report_id':'review','context_revision':0,'question':task['question'],'answer':'Not sure yet','review_task':{k:task[k] for k in ('id','signature')}})
        saved=WorkspaceStore(self.state.reports).design_context(context_key(p))
        self.assertEqual(result['revision'],1)
        self.assertEqual(next(iter(saved['question_answers'].values()))['review_task']['id'],task['id'])
        data=asdict(p);data['design_context']=saved;data['before_ordering']=before_ordering(data)
        self.assertNotIn(task['id'],[t['id'] for t in review_inputs(data)['tasks']])
        self.assertTrue(all(r['status']=='missing' for r in data['before_ordering']['checks'] if r['requirement']=='physical'))
    def test_changed_question_is_rejected_before_saving(self):
        task=self.prepare()
        with self.assertRaises(AppError):
            self.state.save_question_answer({'report_id':'review','context_revision':0,'question':task['question'],'answer':'yes','review_task':{'id':task['id'],'signature':'wrong'}})
        self.assertFalse(self.state.store.design_context(context_key(self.state.project)))

class LedgerInterviewTests(unittest.TestCase):
    def data(self,expert=True):
        data=design();data['design_context']={'product_context':{'experience':'Experienced EE — show electrical limits'} if expert else {}};data['verification_results']=evaluate(data);data['before_ordering']=before_ordering(data)
        return data
    def pipeline(self):
        from pcb_checker.ai.origin import RECONCILE_PREFIX
        return {'product_model':{'intent_questions':[{'id':'a','kind':'purpose','question':'What is this product meant to do?','context':'x'}]},
                'ledger':{'claims':[{'id':'H001','origin':'scope','statement':'s','status':'supported',
                    'open_questions':[f'{RECONCILE_PREFIX} (R001: Keep U1 as placed.)','What value should the U1 decoupling capacitor be?']}]}}
    def test_intent_and_reconcile_questions_reach_experts(self):
        from pcb_checker.ai.origin import RECONCILE_PREFIX
        data=self.data();data['ai_review']={'status':'completed','result':{'questions':[],'pipeline':self.pipeline()}}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertNotIn('What is this product meant to do?',got)
        self.assertTrue(any('meant to do' in u['need'] for u in out['unknowns']))
        self.assertTrue(any(q.startswith(RECONCILE_PREFIX) for q in got))
        self.assertFalse(any('decoupling capacitor' in q for q in got))
    def test_beginners_get_no_generic_tasks_and_skip_pin_level_reconcile(self):
        from pcb_checker.ai.origin import RECONCILE_PREFIX
        data=self.data(expert=False);data['ai_review']={'status':'completed','result':{'questions':[],'pipeline':self.pipeline()}}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertEqual(got,[])
        self.assertTrue(any('meant to do' in u['need'] for u in out['unknowns']))
        self.assertFalse(any(q.startswith(RECONCILE_PREFIX) for q in got))
    def test_answered_ledger_questions_suppressed_like_ai_questions(self):
        from pcb_checker.ai.origin import RECONCILE_PREFIX
        data=self.data();data['ai_review']={'status':'completed','result':{'questions':[],'pipeline':self.pipeline()}}
        data['design_context']={**data['design_context'],'question_answers':{'a':{'question':'What is this product meant to do?','answer':'A sensor.'}}}
        got=[t['question'] for t in review_inputs(data)['tasks'] if t['kind']=='question']
        self.assertNotIn('What is this product meant to do?',got)
        self.assertTrue(any(q.startswith(RECONCILE_PREFIX) for q in got))
    def test_missing_or_stale_pipeline_is_silent(self):
        for review in ({'status':'stale'},{'status':'completed','result':{}},{'status':'completed','result':{'pipeline':None}}):
            data=self.data();data['ai_review']=review
            self.assertFalse(any(t['kind']=='question' for t in review_inputs(data)['tasks']))
    def test_real_pipeline_questions_flow_end_to_end(self):
        from unittest.mock import patch

        from pcb_checker.ai import investigation as workflow
        from pcb_checker.ai import reviewer
        from test_circuit_audit import fixture
        from test_investigation import plan
        project=fixture();packet=reviewer.evidence_packet(project)
        with patch.object(workflow,'ask_plan',return_value=(plan(),{})):
            _,trace,_=workflow.investigate(project,packet,key='k',model='t',provider='m')
        data=self.data();data['ai_review']={'status':'completed','result':{'questions':[],'pipeline':trace}}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertNotIn('What is this product meant to do?',got)
        self.assertTrue(any('meant to do' in u['need'] for u in out['unknowns']),
                        'generic pipeline intent is preserved, never posed')
    def test_beginner_never_gets_disguised_implementation_questions(self):
        data=self.data();data['design_context']['product_context']['experience']='I can solder and use KiCad'
        quoted=('When you press a key or turn a knob, should this board pull '
                'one shared wire to tap the main board?')
        disguised=('When you press a key, should U1 pull the shared row wire low '
                   'to signal the main board at J4?')
        behavior='What should happen when you turn the knob connected at J4?'
        data['saved_review_questions']=[quoted,disguised,behavior]
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertEqual(got,[behavior])
        self.assertTrue(any(quoted in u['need'] for u in out['unknowns']))
        self.assertTrue(any(disguised in u['need'] for u in out['unknowns']))
        # Experts still get the anchored implementation question.
        data['design_context']['product_context']['experience']='Experienced EE — show electrical limits'
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertIn(disguised,got)
        self.assertIn(behavior,got)

    def test_technical_verification_never_reaches_the_interview(self):
        data=self.data()
        data['ai_review']={'status':'completed','result':{'questions':[]}}
        data['action_plan']={'actions':[{'pin_checks':[{'decision':'needs_context',
            'missing_context':['Can you confirm the SW9 mounting tabs are mechanical only?']}]}]}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertEqual(got,[])
        self.assertTrue(any('SW9' in d['need'] and d['target_agent']=='investigation'
                            for d in out['delegations']),
                        'technical verification must route to investigation, never the user')

    def test_unanchored_mixed_gap_delegates_technical_half_only(self):
        data=self.data()
        data['ai_review']={'status':'completed','result':{'questions':[
            'What waterproof enclosure rating is needed given the regulator thermal dissipation in the intended outdoor operating environment?']}}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertEqual(got,[])
        self.assertTrue(any(d['origin_agent']=='ai_review' for d in out['delegations']),
                        'mixed gaps must still delegate their technical half')
    def test_unclassifiable_origin_question_without_anchor_is_preserved_not_asked(self):
        data=self.data()
        data['ai_review']={'status':'completed','result':{'questions':[],
            'pipeline':{'product_model':{'intent_questions':[
                {'id':'b','kind':'purpose','question':'What distinguishes revision B from the earlier prototype?','context':'x'}]},
                'ledger':{'claims':[]}}}}
        out=review_inputs(data)
        got=[t['question'] for t in out['tasks'] if t['kind']=='question']
        self.assertNotIn('What distinguishes revision B from the earlier prototype?',got)
        self.assertTrue(any('distinguishes revision B' in u['need'] for u in out['unknowns']))
