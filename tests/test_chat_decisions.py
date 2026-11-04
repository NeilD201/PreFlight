from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.chat_decisions import accepted_choices, validated_proposals, prepare, apply_review
from pcb_checker.design_review import context_key, local_plan
from pcb_checker.design_identity import design_identity
from pcb_checker.storage import WorkspaceStore
from pcb_checker.workflow import keyed_groups
from dataclasses import asdict
from test_design_identity import sample
from test_design_review import design_result
from pcb_checker.ai.reviewer import evidence_packet
import test_webapp

class Decisions(unittest.TestCase):
    setUp=test_webapp.StateTests.setUp
    tearDown=test_webapp.StateTests.tearDown

    def setup_project(self):
        p=sample();self.state.project=p;self.state.report_id='saved';self.state.model='meta/Test';self.state.meta_key='test-key'
        self.state.store.save_context(context_key(p),p.design_context,0)
        p.design_context=self.state.store.design_context(context_key(p))
        self.state.store.preference('input:saved',{'path':'test.zip'})
        return p

    def propose(self,p,channel='general'):
        proposal={'kind':'part','target':'U42','value':'ABC123'}
        self.state.store.save_conversation(context_key(p),'saved',channel,'offer','Which part?')
        self.state.store.finish_conversation(context_key(p),channel,'offer',{
            'status':'ai','answer':'I recommend ABC123 for U42.',
            'proposals':validated_proposals(p,[proposal]),
            'decision_basis':{'sources':p.provenance.get('source_hashes',{}),'context_revision':p.design_context['revision']}})
        return proposal

    def review(self,p,disposition='no_change'):
        result=design_result(evidence_packet(p))
        for a in result['actions']:
            a.update(disposition=disposition,steps=[] if disposition=='no_change' else ['Choose whether the product needs external triggering.'])
        return {'status':'completed','result':result}

    def test_confirmation_saved_once_rechecked_and_overlay_survives_restart(self):
        p=self.setup_project();self.propose(p)
        request={'report_id':'saved','question':'ok then use that'}
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'The part choice settles identity only.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value=self.review(p)) as check:
            result=self.state.project_chat(request)
        saved=self.state.store.design_context(context_key(p))
        self.assertEqual(saved['component_intent']['U42']['value'],'ABC123')
        self.assertEqual(result['decision_update']['review_status'],'completed')
        check.assert_called_once();self.assertEqual(check.call_args.kwargs['max_attempts'],1)
        self.assertEqual(check.call_args.args[0].design_context['component_intent']['U42']['value'],'ABC123')
        reopened=WorkspaceStore(self.state.reports)
        record=reopened.preference('chat_review:saved')
        plan=apply_review(local_plan(p),record,p,saved)
        self.assertEqual(plan['actions'][0]['disposition'],'no_change')
        self.assertIn('decision_update',reopened.conversations(context_key(p))[-1]['metadata'])
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Already saved.'}),patch('pcb_checker.ai.reviewer.run_ai_review') as check:
            self.state.project_chat(request)
        check.assert_not_called();self.assertEqual(self.state.store.design_context(context_key(p))['revision'],saved['revision'])

    def test_chat_acceptance_does_not_itself_close_wiring_issue(self):
        p=self.setup_project();self.propose(p)
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Choice accepted.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value=self.review(p,'investigate')):
            result=self.state.project_chat({'report_id':'saved','question':'yes'})
        self.assertEqual(result['decision_update']['resolved'],[])
        self.assertIn('external triggering',result['decision_update']['next_action'])

    def test_failed_recheck_keeps_decision_but_never_hides_issue(self):
        p=self.setup_project();self.propose(p)
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Understood.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value={'status':'failed','message':'Evidence rejected'}):
            result=self.state.project_chat({'report_id':'saved','question':'yes'})
        self.assertEqual(result['decision_update']['review_status'],'incomplete')
        self.assertIsNone(self.state.store.preference('chat_review:saved'))
        self.assertEqual(self.state.store.design_context(context_key(p))['component_intent']['U42']['value'],'ABC123')

    def test_stale_files_save_intent_without_rechecking_or_resolving(self):
        p=self.setup_project();self.propose(p)
        self.state.store.preference('input:saved',{'path':str(self.source)})
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Understood.'}),patch('pcb_checker.ai.reviewer.run_ai_review') as check:
            result=self.state.project_chat({'report_id':'saved','question':'yes'})
        check.assert_not_called();self.assertEqual(result['decision_update']['review_status'],'needs_update')
        self.assertIsNone(self.state.store.preference('chat_review:saved'))

    def test_untrusted_client_history_cannot_authorize_a_decision(self):
        p=self.setup_project()
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Saved!','proposed_decisions':[{'kind':'part','target':'U42','value':'ABC123'}]}),patch('pcb_checker.ai.reviewer.run_ai_review') as check:
            result=self.state.project_chat({'report_id':'saved','question':'yes','history':[{'role':'assistant','text':'Use ABC123 for U42'}]})
        check.assert_not_called();self.assertNotIn('decision_update',result)
        self.assertNotIn('component_intent',self.state.store.design_context(context_key(p)))

    def test_followup_answer_replaces_old_unknown_and_rechecks(self):
        from pcb_checker.chat_decisions import questions
        from pcb_checker.workflow import digest
        p=self.setup_project();q='Should a separate wire start each reading?'
        p.ai_review={'result':{'questions':[],'actions':[{'pin_checks':[{'missing_context':[q]}]}]}}
        current=p.design_context;current['question_answers']={digest(q.casefold()):{'question':q,'answer':'Not sure'}}
        p.design_context=self.state.store.save_context(context_key(p),current,current['revision'])
        target=next(k for k,v in questions(p).items() if v['question']==q)
        proposal={'kind':'answer','target':target,'value':'No external trigger is needed.'}
        self.state.store.save_conversation(context_key(p),'saved','general','offer','What is best?')
        self.state.store.finish_conversation(context_key(p),'general','offer',{'status':'ai','answer':'Software starts each reading.',
          'proposals':validated_proposals(p,[proposal]),'decision_basis':{'sources':p.provenance.get('source_hashes',{}),'context_revision':p.design_context['revision']}})
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Understood.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value={'status':'failed','message':'Unavailable'}):
            result=self.state.project_chat({'report_id':'saved','question':'yes'})
        answers=self.state.store.design_context(context_key(p))['question_answers']
        self.assertEqual(len(answers),1);self.assertEqual(answers[digest(q.casefold())]['answer'],proposal['value'])
        self.assertEqual(result['decision_update']['status'],'saved')

    def test_question_negation_ambiguity_and_unknown_part_are_not_approval(self):
        p=self.setup_project();proposals=validated_proposals(p,[{'kind':'part','target':'U42','value':'ABC123'}])
        for text in ('maybe','should I use ABC123?','do not use ABC123','yes but not that','what does it mean'):
            self.assertEqual(accepted_choices(text,proposals),[])
        self.assertEqual(accepted_choices('yes',proposals+proposals),[])
        self.assertEqual(validated_proposals(p,[{'kind':'part','target':'U42','value':'ABC999'}]),[])
        self.assertEqual(accepted_choices('Confirm: '+proposals[0]['label'],proposals),proposals)

    def test_issue_chat_uses_its_own_saved_proposal(self):
        p=self.setup_project();group=keyed_groups(asdict(p))[0];channel='issue-key:'+group['key'];self.propose(p,channel)
        with patch('pcb_checker.webapp.answer_step',return_value={'status':'ai','answer':'Understood.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value=self.review(p)):
            result=self.state.step_help({'report_id':'saved','group_id':group['id'],'question':'ok then use that','step_index':0})
        self.assertEqual(result['decision_update']['status'],'saved')
        self.assertEqual(self.state.store.conversations(context_key(p),channel)[-1]['metadata']['decision_update']['status'],'saved')

    def test_old_web_recommendation_can_be_confirmed_but_not_unrelated_reply(self):
        p=self.setup_project()
        self.state.store.save_conversation(context_key(p),'saved','general','legacy','Which is better?')
        self.state.store.finish_conversation(context_key(p),'general','legacy',{'status':'ai','answer':'For U42 I would keep **ABC123**.'})
        prepare(self.state.store,p,'general','saved','ok then use that')
        self.assertEqual(len(p.provenance['chat_workflow']['confirmed_this_message']),1)
        p.provenance['source_hashes']={'changed':'file'}
        # A new saved report never inherits an unbound recommendation.
        prepare(self.state.store,p,'general','another-report','yes')
        self.assertEqual(p.provenance['chat_workflow']['confirmed_this_message'],[])

    def test_overlay_expires_when_context_or_source_evidence_changes(self):
        p=self.setup_project();self.propose(p)
        with patch('pcb_checker.webapp.answer_project',return_value={'status':'ai','answer':'Understood.'}),patch('pcb_checker.ai.reviewer.run_ai_review',return_value=self.review(p)):
            self.state.project_chat({'report_id':'saved','question':'yes'})
        saved=self.state.store.design_context(context_key(p));record=self.state.store.preference('chat_review:saved');base=local_plan(p)
        self.assertEqual(apply_review(base,record,p,{**saved,'revision':saved['revision']+1}),base)
        p.provenance['source_hashes']={'other':'revision'}
        self.assertEqual(apply_review(base,record,p,saved),base)

class Aliases(unittest.TestCase):
    def test_library_alias_and_order_heading_after_table(self):
        p=sample();c=p.components['U42']
        for r in c.schematic+c.pcb:r.value='ABC123_T';r.properties={'MP':'ABC123+T'}
        doc=p.datasheets['documents']['C150772'];doc['mpn']='ABC123+T'
        doc['pages']=[{'page':28,'text':'PART NUMBER TEMP RANGE PIN-PACKAGE\nABC123+T 0C to 70C LGA14\nOrdering Information'}]
        identity=design_identity(p,'U42')
        self.assertEqual(identity['status'],'documented');self.assertTrue(identity['library_aliases'])
        self.assertEqual(identity['document']['page'],28)
        c.pcb[0].value='ABC124_T'
        self.assertEqual(design_identity(p,'U42')['status'],'conflict')

if __name__=='__main__':unittest.main()
