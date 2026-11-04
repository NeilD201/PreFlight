import sys,tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.web_chat import answer_web,extract_answer
from pcb_checker.ai.reviewer import AIError
from pcb_checker.ai.usage import request_budget
from pcb_checker.storage import WorkspaceStore

class WebChatTests(unittest.TestCase):
    def response(self):
        return {'status':'completed','output':[{'type':'web_search_call'}, {'type':'message','content':[{'type':'output_text','text':'Read this [1].','annotations':[{'type':'url_citation','start_index':10,'end_index':13,'url':'https://example.com/doc','title':'Official document'}]}]}]}
    def test_sources_and_request_limits(self):
        with patch('pcb_checker.web_chat._request',return_value=self.response()) as send:
            result=answer_web({'question':'Read documentation'},key='secret',model='gpt-6-luna')
        self.assertTrue(result['web_searched']);self.assertEqual(len(result['citations']),1)
        payload=send.call_args.args[1]
        self.assertEqual(payload['max_tool_calls'],2)
        from pcb_checker.answer_style import CHAT_STYLE
        self.assertIn(CHAT_STYLE,payload['instructions'])
        self.assertEqual(payload['tools'][0]['type'],'web_search')
        self.assertFalse(payload['store']);self.assertEqual(send.call_args.kwargs['max_attempts'],1)
    def test_rejects_unsafe_links_and_incomplete_output(self):
        response=self.response();ann=response['output'][1]['content'][0]['annotations'][0]
        ann['url']='javascript:alert(1)'
        self.assertEqual(extract_answer(response)['citations'],[])
        response['status']='incomplete'
        with self.assertRaises(AIError):extract_answer(response)
    def test_no_search_and_errors_are_honest(self):
        response=self.response();response['output'].pop(0)
        self.assertFalse(extract_answer(response)['web_searched'])
        with patch('pcb_checker.web_chat._request',side_effect=AIError('failed secret')):
            self.assertNotIn('secret',answer_web({},key='secret',model='gpt-6-luna')['message'])
        with patch('pcb_checker.web_chat._request') as send:
            self.assertEqual(answer_web({},key='secret',model='gemini')['status'],'failed');send.assert_not_called()
    def test_web_chat_preserves_proposals_and_citations_without_extra_request(self):
        response=self.response()
        response['output'][1]['content'][0]['text']=json.dumps({'answer':'Use ABC123.','proposals':[{'kind':'part','target':'U1','value':'ABC123'}]})
        with patch('pcb_checker.web_chat._request',return_value=response) as send:
            result=answer_web({'question':'Which part?','chat_workflow':{}},key='secret',model='gpt-6-luna')
        send.assert_called_once()
        self.assertEqual(result['answer'],'Use ABC123.')
        self.assertEqual(result['proposed_decisions'][0]['target'],'U1')
        self.assertEqual(result['citations'][0]['start'],len(result['answer']))
        self.assertEqual(send.call_args.args[1]['text']['format']['type'],'json_schema')

    def test_sources_survive_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store=WorkspaceStore(root);store.save_conversation('p','r','general','q','Question')
            result=extract_answer(self.response());store.finish_conversation('p','general','q',result)
            restored=WorkspaceStore(root).conversations('p','general')[0]
            self.assertEqual(restored['metadata']['citations'],result['citations'])
            self.assertTrue(restored['metadata']['web_searched'])
    def test_web_off_preserves_existing_chat(self):
        from pcb_checker.project_chat import answer_project
        with patch('pcb_checker.project_chat.answer_packet',return_value={'status':'ai'}) as local,patch('pcb_checker.web_chat.answer_web') as web:
            answer_project(None,'Hello',[],key='secret',model='gpt-6-luna',web_search=False)
            local.assert_called_once();web.assert_not_called()
    def test_meta_model_uses_grounded_search_not_openai(self):
        grounded = {'status': 'completed', 'text': 'Grounded answer.',
                    'citations': [{'start': 0, 'end': 7, 'url': 'https://example.com/s', 'title': 'S'}],
                    'searched': True, 'usage': {}}
        with patch('pcb_checker.ai.meta.post_responses_grounded', return_value=grounded) as send:
            result = answer_web({'question': 'What is X?'}, key='secret', model='meta/muse-spark-1.3-contributor')
        send.assert_called_once()
        self.assertEqual(result['status'], 'ai')
        self.assertTrue(result['web_searched'])
        self.assertEqual(result['citations'][0]['url'], 'https://example.com/s')
        self.assertNotIn('secret', json.dumps(result))

    def test_meta_unsearched_answer_is_flagged_not_failed(self):
        with patch('pcb_checker.ai.meta.post_responses_grounded',
                   return_value={'status': 'completed', 'text': 'Best effort.', 'citations': [],
                                 'searched': False, 'usage': {}}):
            result = answer_web({'question': 'Q?'}, key='secret', model='muse-spark-1.3-contributor')
        self.assertEqual(result['status'], 'ai')
        self.assertFalse(result['web_searched'])
        self.assertEqual(result['citations'], [])

    def test_meta_skipped_search_retries_once_then_flags(self):
        missed = {'status': 'completed', 'text': 'Best effort.', 'citations': [], 'searched': False, 'usage': {}}
        with patch('pcb_checker.ai.meta.post_responses_grounded', side_effect=[missed, missed]) as send:
            result = answer_web({'question': 'Q?'}, key='secret', model='muse-spark-1.3-contributor')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['status'], 'ai')
        self.assertFalse(result['web_searched'])

    def test_meta_search_retry_succeeds_on_second_attempt(self):
        missed = {'status': 'completed', 'text': 'Best effort.', 'citations': [], 'searched': False, 'usage': {}}
        found = {'status': 'completed', 'text': 'Grounded answer.', 'citations': [], 'searched': True, 'usage': {}}
        with patch('pcb_checker.ai.meta.post_responses_grounded', side_effect=[missed, found]) as send:
            result = answer_web({'question': 'Q?'}, key='secret', model='muse-spark-1.3-contributor')
        self.assertEqual(send.call_count, 2)
        self.assertTrue(result['web_searched'])
        self.assertEqual(result['answer'], 'Grounded answer.')

    def test_meta_workflow_prose_retries_for_json(self):
        prose = {'status': 'completed', 'text': 'Just use a smaller value.', 'citations': [], 'searched': True, 'usage': {}}
        fixed = {'status': 'completed', 'text': json.dumps({'answer': 'Use 1k.', 'proposals': []}), 'citations': [], 'searched': True, 'usage': {}}
        with patch('pcb_checker.ai.meta.post_responses_grounded', side_effect=[prose, fixed]) as send:
            result = answer_web({'question': 'Q?', 'chat_workflow': {}}, key='secret', model='muse-spark-1.3-contributor')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['answer'], 'Use 1k.')
        self.assertEqual(result['proposed_decisions'], [])

    def test_meta_workflow_prose_twice_fails_bounded(self):
        prose = {'status': 'completed', 'text': 'Just use a smaller value.', 'citations': [], 'searched': True, 'usage': {}}
        with patch('pcb_checker.ai.meta.post_responses_grounded', side_effect=[prose, prose]) as send:
            result = answer_web({'question': 'Q?', 'chat_workflow': {}}, key='secret', model='muse-spark-1.3-contributor')
        self.assertEqual(send.call_count, 2)
        self.assertEqual(result['status'], 'failed')

    def test_meta_search_failure_redacts_key(self):
        with patch('pcb_checker.ai.meta.post_responses_grounded', side_effect=AIError('failed secret')):
            result = answer_web({'question': 'Q?'}, key='secret', model='meta/muse-spark-1.3-contributor')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['web_searched'])
        self.assertNotIn('secret', result['message'])

    def test_budget_exhaustion_sends_nothing(self):
        with request_budget('economy','chat') as budget:
            budget.requests=budget.limit
            result=answer_web({},key='secret',model='gpt-6-luna')
            self.assertEqual(result['status'],'failed');self.assertIn('request limit',result['message'])
