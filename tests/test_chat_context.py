import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.chat_context import compact_chat, encode, compact_product
from pcb_checker.step_help import answer_packet, _packet
from pcb_checker.project_chat import answer_project
from test_design_review import project, with_doc
from pcb_checker.design_review import local_plan

class ContextTests(unittest.TestCase):
    def test_duplicate_answers_collapse_without_changing_the_declaration(self):
        value={'answers':{'mode':'Not sure yet'},'answered_questions':[{'id':'mode','question':'What mode?','answer':'Not sure yet'}],
            'questions':[{'id':'mode','question':'What mode?'}], 'source':'User declaration',
            'previous_revision_answers':{'old':'5V'},'previous_revision_questions':{'old':'Supply?'}}
        result=compact_product(value)
        self.assertEqual(result['saved_answers'],[{'id':'mode','question':'What mode?','answer':'Not sure yet'}])
        self.assertIn('Historical',result['previous_revision_answers'][0]['status'])
        self.assertNotIn('answered_questions',result)

    def test_oversized_context_sends_one_request_and_preserves_current_intent(self):
        p=with_doc(project());p.design_context['description']='Battery logger; never connect to mains.'
        p.design_context['product_context']={'purpose':'Battery logger'}
        p.design_context['conversation_memory']=[{'question':'Older discussion '+str(i),'answer':'x'*3900} for i in range(40)]
        p.provenance['source_hashes']={'board':'actual-current-sha'}
        group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        history=[{'role':'user' if i%2==0 else 'assistant','text':str(i)+'x'*3900} for i in range(6)]
        packet=_packet(p,group,action,0,'What should I change on U42?',history)
        original=copy.deepcopy(packet)
        reply={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{"answer":"Example answer"}'}]}]}
        with patch('pcb_checker.step_help.post_ai',return_value=reply) as post:
            result=answer_packet(packet,'Instructions',key='fake',model='test')
        self.assertEqual(result['status'],'ai',result);post.assert_called_once()
        from pcb_checker.answer_style import CHAT_STYLE
        self.assertIn(CHAT_STYLE,post.call_args.args[0]['system_instruction'])
        sent=post.call_args.args[0]['input'];data=json.loads(sent)
        self.assertLessEqual(len(sent.encode()),48000)
        self.assertEqual(data['question'],original['question'])
        self.assertEqual(data['selected_step'],original['selected_step'])
        self.assertEqual(data['recent_chat'],history[-2:])
        self.assertEqual(data['project_context']['source_revision'],{'board':'actual-current-sha'})
        self.assertIn('never connect to mains',data['project_context']['device_description'])
        self.assertIn('datasheet_excerpts',data)
        self.assertEqual(packet,original,'Selection never mutates saved project data')
        self.assertTrue(result['context_usage']['compacted'])

    def test_unfinished_answer_retries_once_with_compacted_context(self):
        p=with_doc(project());group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        packet=_packet(p,group,action,0,'What should I change on U42?',[])
        packet['datasheet_excerpts']=[{'evidence_id':'D:%d' % i,'references':['U42'],
            'mpn':'X','part_match':'verified','page':1,'text':'Excerpt body '+str(i)+'x'*2000,'url':''} for i in range(8)]
        good={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{"answer":"Example answer"}'}]}]}
        calls=[]
        def fake(request, key, **kw):
            calls.append(bytearray(request['input']).decode() if isinstance(request['input'], (bytes, bytearray)) else request['input'])
            if len(calls) == 1:
                return {'status': 'incomplete'}
            return good
        with patch('pcb_checker.step_help.post_ai', side_effect=fake):
            result=answer_packet(packet,'Instructions',key='fake',model='test')
        self.assertEqual(result['status'],'ai',result)
        self.assertEqual(len(calls),2,'exactly one bounded retry on an unfinished answer')
        import json as _json
        self.assertLess(len(calls[1].encode()),len(calls[0].encode()),'retry compacts the context')
        self.assertEqual(_json.loads(calls[1])['question'],'What should I change on U42?')
        with patch('pcb_checker.step_help.post_ai',return_value={'status':'incomplete'}) as post2:
            twice=answer_packet(_packet(p,group,action,0,'Q?',[]),'Instructions',key='fake',model='test')
        self.assertEqual(twice['status'],'failed')
        self.assertIn('did not finish',twice['message'])
        self.assertEqual(post2.call_count,2,'persistent truncation still tries exactly one retry, then fails visibly')

    def test_truncation_exception_retries_once_with_compacted_context(self):
        from pcb_checker.ai.reviewer import AIError
        p=with_doc(project());group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        packet=_packet(p,group,action,0,'What should I change on U42?',[])
        packet['datasheet_excerpts']=[{'evidence_id':'D:%d' % i,'references':['U42'],
            'mpn':'X','part_match':'verified','page':1,'text':'Excerpt body '+str(i)+'x'*2000,'url':''} for i in range(8)]
        good={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{"answer":"Example answer"}'}]}]}
        calls=[]
        def fake(request, key, **kw):
            calls.append(request)
            if len(calls) == 1:
                raise AIError('Meta stopped early: the answer exceeded the output limit. No partial edit or conclusion was accepted. Narrow the request scope and run again.',
                              diagnostics={'error_kind': 'truncated', 'retryable': False, 'finish_reason': 'length'})
            return good
        with patch('pcb_checker.step_help.post_ai', side_effect=fake):
            result=answer_packet(packet,'Instructions',key='fake',model='test')
        self.assertEqual(result['status'],'ai',result)
        self.assertEqual(len(calls),2,'a truncation raise must trigger exactly one compacted retry')
        self.assertIn('800 characters',calls[1]['system_instruction'],
            'an output-limit retry must cap the requested answer length; shrinking input alone re-truncates')
        with patch('pcb_checker.step_help.post_ai', side_effect=AIError(
                'Meta stopped early.', diagnostics={'error_kind': 'truncated', 'retryable': False})) as post2:
            twice=answer_packet(_packet(p,group,action,0,'Q?',[]),'Instructions',key='fake',model='test')
        self.assertEqual(twice['status'],'failed')
        self.assertEqual(post2.call_count,2,'persistent truncation still tries exactly one retry, then fails visibly')

    def test_large_documents_are_omitted_whole_and_disclosed(self):
        packet={'question':'U42 pin 1?', 'datasheet_excerpts':[
            {'evidence_id':'D:big','references':['U42'],'text':'⚡'*30000},
            {'evidence_id':'D:small','references':['U42'],'text':'Connect pin 1 to ground.'}],
            'components':[{'reference':'U42','pins':[{'number':'1','net':'GND'}]}]}
        result,stats=compact_chat(packet)
        self.assertEqual(result['datasheet_excerpts'],[packet['datasheet_excerpts'][1]])
        self.assertIn('datasheet_excerpts (1)',result['context_limits'][-1])
        self.assertLessEqual(len(encode(result).encode()),48000)
        self.assertGreater(stats['original_bytes'],stats['sent_bytes'])

    def test_general_chat_uses_same_compaction_without_extra_summary_request(self):
        p=project();p.design_context['conversation_memory']=[{'question':'Old '+str(i),'answer':'a'*4000} for i in range(40)]
        reply={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{"answer":"Hello"}'}]}]}
        with patch('pcb_checker.step_help.post_ai',return_value=reply) as post:
            result=answer_project(p,'Tell me about U42',[],key='fake',model='test')
        self.assertEqual(result['status'],'ai',result);post.assert_called_once()
        from pcb_checker.answer_style import CHAT_STYLE
        self.assertIn(CHAT_STYLE,post.call_args.args[0]['system_instruction'])
        self.assertLessEqual(len(post.call_args.args[0]['input'].encode()),48000)

    def test_small_packets_are_not_lossily_rewritten(self):
        packet={'question':'Hi','recent_chat':[{'role':'user','text':'Remember 3.3 V'}],'project':None}
        compact,stats=compact_chat(packet)
        self.assertEqual(compact,packet);self.assertFalse(stats['compacted'])

    def test_retry_with_prior_revision_answers_recompacts_without_attribute_error(self):
        # Incident 80ab5aec: a project with previous-revision answers compacts
        # previous_revision_answers from a dict into a list of records. The
        # bounded output-limit retry feeds that compacted packet back through
        # compact_chat, which must accept the list form instead of raising
        # AttributeError on .items().
        p=with_doc(project());group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        packet=_packet(p,group,action,0,'Why is this step needed?',[])
        packet['project_context']['product_context']['previous_revision_answers']={'old-supply':'5 V'}
        packet['project_context']['product_context']['previous_revision_questions']={'old-supply':'What supply?'}
        packet['datasheet_excerpts']=[{'evidence_id':'D:%d' % i,'references':['U42'],
            'mpn':'X','part_match':'verified','page':1,'text':'Excerpt body '+str(i)+'x'*2000,'url':''} for i in range(8)]
        good={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{\"answer\":\"Example answer\"}'}]}]}
        calls=[]
        def fake(request, key, **kw):
            calls.append(request)
            if len(calls) == 1:
                return {'status': 'incomplete'}
            return good
        with patch('pcb_checker.step_help.post_ai', side_effect=fake):
            result=answer_packet(packet,'Instructions',key='fake',model='test',byte_limit=20000)
        self.assertEqual(result['status'],'ai',result)
        self.assertEqual(len(calls),2,'the retry must reach the model instead of crashing in recompaction')

    def test_compact_product_accepts_compacted_list_form(self):
        once=compact_product({'previous_revision_answers':{'old-supply':'5 V'},
            'previous_revision_questions':{'old-supply':'What supply?'}})
        self.assertEqual(once['previous_revision_answers'][0]['answer'],'5 V')
        twice=compact_product(once)
        self.assertEqual(twice['previous_revision_answers'][0]['answer'],'5 V')
        self.assertIn('Historical',twice['previous_revision_answers'][0]['status'])

if __name__=='__main__':unittest.main()
