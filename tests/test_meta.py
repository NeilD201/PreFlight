"""First-party Meta contract, shared callers, and isolated credential persistence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.meta import DEFAULT_MODEL, ENDPOINT
from pcb_checker.ai.reviewer import AIError, post_ai, run_ai_review, check_ai_connection
from pcb_checker.step_help import answer_step
from pcb_checker.design_review import local_plan
from pcb_checker.webapp import AppState, AppError
from test_design_review import project
from test_ai_review import RESULT
from pcb_checker.ai.reviewer import _object, STR


def payload():
    return {'model': 'meta/placeholder', 'store': False, 'input': 'Preserve the supplied board evidence.',
            'system_instruction': 'Explain the selected issue.',
            'generation_config': {'max_output_tokens': 1400, 'thinking_level': 'low'},
            'response_format': {'type': 'text', 'mime_type': 'application/json', 'schema': _object({'answer': STR})}}
from test_credentials import CredentialTests

MODEL='meta/'+DEFAULT_MODEL

def response(value):
    return {'choices':[{'message':{'role':'assistant','content':json.dumps(value)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}

class MetaTests(unittest.TestCase):
    def test_native_contract_keeps_complete_context_and_uses_only_meta_key(self):
        packet=payload();packet['model']=MODEL;messages=[]
        with patch('pcb_checker.ai.reviewer._request',return_value=response({'answer':'Test'})) as send:
            result=post_ai(packet,'meta-test',progress=messages.append)
        url,body,auth,provider=send.call_args.args
        self.assertEqual(url,ENDPOINT);self.assertEqual(provider,'meta')
        self.assertEqual(auth,{'Authorization':'Bearer meta-test'})
        self.assertEqual(body['model'],DEFAULT_MODEL)
        self.assertEqual(body['messages'],[{'role':'system','content':'Your assistant name is Vector. '+packet['system_instruction']},{'role':'user','content':packet['input']}])
        self.assertEqual(body['response_format']['json_schema']['schema'],packet['response_format']['schema'])
        self.assertNotIn('strict',body['response_format']['json_schema'])
        for field in ('store','reasoning','generation_config','input'):self.assertNotIn(field,body)
        self.assertEqual(body['max_completion_tokens'],1400);self.assertFalse(body['stream'])
        self.assertEqual(result['usage']['total_tokens'],15)
        self.assertEqual(send.call_args.kwargs['max_attempts'],3)
        self.assertIn('Asking Vector…',messages)

    def test_only_contributor_model_is_accepted(self):
        from pcb_checker.ai.meta import model_id
        self.assertEqual(model_id('meta/'+DEFAULT_MODEL),DEFAULT_MODEL)
        self.assertEqual(model_id(DEFAULT_MODEL),DEFAULT_MODEL)
        for other in ('meta/muse-wonder-1.0','meta/muse-spark-1.3','meta/Llama-4-Maverick-17B-128E-Instruct-FP8',
                      'meta/https://evil.test','gpt-6-luna','',None):
            with self.subTest(model=other),self.assertRaises(ValueError):
                model_id(other)
        packet=payload();packet['model']='meta/muse-wonder-1.0'
        with patch('pcb_checker.ai.reviewer._request') as send:
            with self.assertRaises(ValueError):post_ai(packet,'meta-test')
        send.assert_not_called()

    def test_incomplete_or_tool_call_never_becomes_an_edit(self):
        packet=payload();packet['model']=MODEL
        cases=[{'message':{'role':'assistant','content':'{}'},'finish_reason':'length'},
               {'message':{'role':'assistant','content':'{}','tool_calls':[{'id':'x'}]},'finish_reason':'tool_calls'},
               {'message':{'role':'assistant','content':'{}','refusal':'declined'},'finish_reason':'stop'},
               {'message':{'role':'assistant','content':''},'finish_reason':'stop'},
               {'message':{'role':'assistant'},'finish_reason':'stop'}]
        for choice in cases:
            with self.subTest(choice=choice),patch('pcb_checker.ai.reviewer._request',return_value={'choices':[choice]}) as send:
                with self.assertRaises(AIError):post_ai(packet,'meta-test')
                send.assert_called_once()
        with patch('pcb_checker.ai.reviewer._request',return_value={'choices':[]}) as send:
            with self.assertRaises(AIError):post_ai(packet,'meta-test')
            send.assert_called_once()

    def test_truncation_names_the_limit_and_forbids_identical_retry(self):
        packet = payload(); packet['model'] = MODEL
        truncated = {'message': {'role': 'assistant', 'content': '{"answer":"half'}, 'finish_reason': 'length'}
        with patch('pcb_checker.ai.reviewer._request', return_value={'choices': [truncated]}) as send:
            with self.assertRaises(AIError) as caught:
                post_ai(packet, 'meta-test')
        send.assert_called_once()
        self.assertIn('output limit', str(caught.exception))
        self.assertIn('Narrow the request scope', str(caught.exception))
        self.assertEqual(caught.exception.diagnostics['error_kind'], 'truncated')
        self.assertFalse(caught.exception.diagnostics['retryable'])

    def test_meta_retries_transient_503_then_succeeds(self):
        chat = {'choices': [{'message': {'role': 'assistant', 'content': '{"answer":"Test"}'},
                                         'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(chat).encode(),
        })()
        packet = payload(); packet['model'] = MODEL
        outage = HTTPError('https://example.com', 503, 'temporary', {}, None)
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = [outage, response]
            result = post_ai(packet, 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(sleep.call_args_list[0].args, (5,))

    def test_meta_reports_503_after_retry_budget(self):
        packet = payload(); packet['model'] = MODEL
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [
                HTTPError('https://example.com', 503, 'temporary', {}, None),
                HTTPError('https://example.com', 503, 'temporary', {}, None),
                HTTPError('https://example.com', 503, 'temporary', {}, None),
            ]
            with self.assertRaises(AIError) as caught:
                post_ai(packet, 'meta-test-secret')
        self.assertIn('after 3 attempts', str(caught.exception))
        self.assertTrue(caught.exception.diagnostics['retryable'])
        self.assertEqual(caught.exception.diagnostics['http_status'], 503)

    def test_meta_retries_transient_timeout_but_not_rejection(self):
        chat = {'choices': [{'message': {'role': 'assistant', 'content': '{"answer":"Test"}'},
                                         'finish_reason': 'stop'}], 'usage': {}}
        response = type('Response', (), {
            '__enter__': lambda self: self,
            '__exit__': lambda self, *args: None,
            'read': lambda self, limit: json.dumps(chat).encode(),
        })()
        packet = payload(); packet['model'] = MODEL
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [URLError(TimeoutError('timed out')), response]
            result = post_ai(packet, 'meta-test-secret')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(opener.return_value.open.call_count, 2)
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
                patch('pcb_checker.ai.reviewer.time.sleep') as sleep:
            opener.return_value.open.side_effect = HTTPError('https://example.com', 401, 'denied', {}, None)
            with self.assertRaises(AIError) as caught:
                post_ai(packet, 'meta-test-secret')
        opener.return_value.open.assert_called_once()
        sleep.assert_not_called()
        self.assertFalse(caught.exception.diagnostics['retryable'])

    def test_text_parts_and_unknown_usage(self):
        packet=payload();packet['model']=MODEL
        result=response({})
        result['choices'][0]['message']['content']=[{'type':'text','text':'{}'}]
        result['usage']={'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,
                         'prompt_tokens_details':{'cached_tokens':4}}
        with patch('pcb_checker.ai.reviewer._request',return_value=result):
            normalized=post_ai(packet,'meta-test')
        self.assertEqual(normalized['steps'][0]['content'][0]['text'],'{}')
        self.assertEqual(normalized['usage']['cached_input_tokens'],4)
        result['usage']={'other':100}
        with patch('pcb_checker.ai.reviewer._request',return_value=result):
            normalized=post_ai(packet,'meta-test')
        self.assertEqual(normalized['usage'],{})

    def test_review_and_chat_use_meta_and_existing_validation(self):
        p=project()
        with patch('pcb_checker.ai.reviewer._request',return_value=response(RESULT)) as send:
            result=run_ai_review(p,key='meta-test',model=MODEL)
        self.assertEqual(result['status'],'completed',result);self.assertEqual(result['provider'],'Meta')
        self.assertEqual(result['usage']['input_tokens'],10)
        with patch('pcb_checker.ai.reviewer._request',return_value=response({'answer':'Select the highlighted pad.'})) as send:
            result=answer_step(p,p.review['groups'][0],local_plan(p)['actions'][0],0,'How?',[],key='meta-test',model=MODEL)
        self.assertEqual(result['status'],'ai',result);self.assertEqual(result['provider_label'],'Vector')
        self.assertEqual(send.call_args.args[3],'meta')

    def test_cli_meta_provider_and_connection_test(self):
        with patch('pcb_checker.ai.reviewer._request',return_value=response({'summary':'Connection OK','answer':'','findings':[],'questions':[],'limitations':[]})) as send:
            result=check_ai_connection(key='meta-test',provider='meta',model=DEFAULT_MODEL)
        self.assertEqual(result['status'],'completed',result);self.assertEqual(result['provider'],'Meta')
        self.assertEqual(send.call_count,1)

    def test_errors_never_switch_provider(self):
        packet=payload();packet['model']=MODEL
        with patch('pcb_checker.ai.reviewer._request',side_effect=AIError('Unavailable')) as send:
            with self.assertRaises(AIError):post_ai(packet,'meta-test')
        send.assert_called_once();self.assertEqual(send.call_args.args[3],'meta')

class MetaCredentials(unittest.TestCase):
    setUp=CredentialTests.setUp
    def test_persistence_is_separate_and_secrets_never_enter_reports(self):
        self.state.settings({'key':'gemini-secret','remember_key':True})
        self.state.settings({'model':'gpt-6-luna','openai_key':'sk-test-secret','remember_key':True})
        old=dict(self.saved)
        config=self.state.settings({'model':MODEL,'meta_key':'meta-secret','remember_key':True})
        self.assertEqual(config['provider'],'Meta');self.assertTrue(config['key_saved'])
        self.assertEqual(self.state.keys_for_ai(),'meta-secret')
        from pcb_checker.credential_store import META_SERVICE, meta_account
        self.assertEqual(self.services[meta_account()],META_SERVICE)
        self.assertEqual(META_SERVICE,b'preflightinapp')
        for account,value in old.items():self.assertEqual(self.saved[account],value)
        self.assertNotIn('meta-secret',json.dumps(config))
        second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
        second.keychain_thread.join(2)
        self.assertEqual(second.keys_for_ai(),'meta-secret')
        self.assertEqual(second.key,'gemini-secret');self.assertEqual(second.openai_key,'sk-test-secret')
        for path in (self.folder/'reports').rglob('*'):
            if path.is_file():self.assertNotIn(b'meta-secret',path.read_bytes())
        second.settings({'model':MODEL,'meta_key':''})
        self.assertEqual(second.keys_for_ai(),'meta-secret')
        second.settings({'model':'gpt-6-luna','clear_key':True})
        self.assertFalse(self.saved);self.assertFalse(second.meta_key)

    def test_no_meta_key_does_not_use_existing_other_keys(self):
        self.state.settings({'key':'gemini-secret','remember_key':False})
        config=self.state.settings({'model':MODEL,'remember_key':False})
        self.assertFalse(config['key_configured']);self.assertFalse(self.state.keys_for_ai())
        self.assertEqual(self.state.key,'gemini-secret')

    def test_session_removal_and_invalid_model(self):
        self.state.settings({'model':MODEL,'meta_key':'meta-secret','remember_key':True})
        self.state.settings({'model':MODEL,'remember_key':False})
        self.assertFalse(self.saved);self.assertEqual(self.state.meta_key,'meta-secret')
        with self.assertRaises(AppError):self.state.settings({'model':'meta/https://evil.test'})
        with self.assertRaises(AppError):self.state.settings({'model':'meta/muse-wonder-1.0'})
        self.state.settings({'model':MODEL,'clear_key':True})
        self.assertFalse(self.state.meta_key)

    def test_new_key_invalidates_successful_test(self):
        self.state.settings({'model':MODEL,'meta_key':'meta-secret','remember_key':False})
        self.state.connection_test={'model':MODEL,'status':'completed','credential_revision':self.state._credential_revision}
        self.assertIsNotNone(self.state.config()['connection_test'])
        self.state.settings({'model':MODEL,'meta_key':'meta-new','remember_key':False})
        self.assertIsNone(self.state.config()['connection_test'])

class MetaAdoptionTests(unittest.TestCase):
    """Server startup recognizes a pre-existing Meta Keychain entry it never recorded."""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.saved={}
        saved=self.saved
        class Store:
            def __init__(self,account,service=None):self.account=account
            def load(self):return saved.get(self.account,'')
            def save(self,value):saved[self.account]=value
            def delete(self):saved.pop(self.account,None)
        self.patch=patch('pcb_checker.webapp.Keychain',Store);self.patch.start();self.addCleanup(self.patch.stop)
        available=patch('pcb_checker.webapp.keychain_available',return_value=True)
        available.start();self.addCleanup(available.stop)

    def adopt(self,**kwargs):
        state=AppState(self.folder,self.folder/'reports',adopt_keychain=True,**kwargs)
        self.addCleanup(state.close)
        if state.keychain_thread:state.keychain_thread.join(5)
        return state

    def test_unrecorded_entry_is_adopted_and_selects_meta(self):
        from pcb_checker.credential_store import meta_account
        self.saved[meta_account()]='adopted-secret'
        state=self.adopt()
        self.assertEqual(state.keys_for_ai(),'adopted-secret')
        self.assertEqual(state.model,MODEL)
        config=state.config()
        self.assertTrue(config['key_configured']);self.assertEqual(config['provider'],'Meta')
        self.assertTrue(config['meta_key_saved']);self.assertFalse(config['keychain_loading'])
        self.assertTrue(state.store.preference('remember_meta_key'))
        self.assertEqual(state.store.preference('ai_model'),MODEL)
        self.assertIsNone(config['connection_test'])
        self.assertNotIn('adopted-secret',json.dumps(config))

    def test_explicit_opt_out_is_never_adopted(self):
        from pcb_checker.credential_store import meta_account
        from pcb_checker.storage import WorkspaceStore
        WorkspaceStore(self.folder/'reports').preference('remember_meta_key',False)
        self.saved[meta_account()]='adopted-secret'
        state=self.adopt()
        self.assertFalse(state.meta_key);self.assertFalse(state.config()['key_configured'])
        self.assertFalse(state.config()['keychain_loading'])

    def test_explicit_provider_choice_is_not_overridden(self):
        from pcb_checker.credential_store import meta_account
        from pcb_checker.storage import WorkspaceStore
        WorkspaceStore(self.folder/'reports').preference('ai_model','gemini-3.5-flash-lite')
        self.saved[meta_account()]='adopted-secret'
        state=self.adopt()
        self.assertEqual(state.model,'gemini-3.5-flash-lite')
        self.assertEqual(state.meta_key,'adopted-secret')
        self.assertTrue(state.config()['meta_key_configured'])

    def test_missing_entry_resolves_gracefully(self):
        state=self.adopt()
        self.assertFalse(state.config()['keychain_loading'])
        self.assertEqual(state.key_storage_error,'')
        self.assertFalse(state.config()['key_configured'])
        self.assertIsNone(state.store.preference('remember_meta_key'))

    def test_no_adoption_without_server_startup_flag(self):
        from pcb_checker.credential_store import meta_account
        self.saved[meta_account()]='adopted-secret'
        state=AppState(self.folder,self.folder/'reports');self.addCleanup(state.close)
        self.assertIsNone(state.keychain_thread)
        self.assertFalse(state.meta_key)

class MetaGroundedSearchTests(unittest.TestCase):
    def payload(self, output, usage=None):
        return {'id': 'resp_1', 'status': 'completed', 'output': output,
                'usage': usage or {'input_tokens': 100, 'output_tokens': 50, 'total_tokens': 150}}

    def searched(self):
        return self.payload([
            {'type': 'web_search_call', 'id': 'ws_1'},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': 'X is 5 V [1].',
                'annotations': [{'type': 'url_citation', 'start_index': 8, 'end_index': 11,
                                 'url': 'https://example.com/spec', 'title': 'Spec'}]}]}])

    def test_grounded_answer_reports_search_proof_and_citations(self):
        from pcb_checker.ai.meta import post_responses_grounded
        with patch('pcb_checker.ai.reviewer._request', return_value=self.searched()) as send:
            result = post_responses_grounded(instructions='I', input_text='Q?', key='secret')
        url, body, _auth, _provider = send.call_args.args
        from pcb_checker.ai.meta import RESPONSES_ENDPOINT
        self.assertEqual(url, RESPONSES_ENDPOINT)
        self.assertEqual(body['tools'], [{'type': 'web_search'}])
        self.assertTrue(result['searched'])
        self.assertEqual(len(result['citations']), 1)
        self.assertEqual(result['citations'][0]['url'], 'https://example.com/spec')
        self.assertEqual(result['text'], 'X is 5 V [1].')

    def test_unsearched_text_is_flagged_never_presented_as_verified(self):
        from pcb_checker.ai.meta import post_responses_grounded
        plain = self.payload([{'type': 'message', 'content': [{'type': 'output_text', 'text': 'Ungrounded guess.'}]}])
        with patch('pcb_checker.ai.reviewer._request', return_value=plain):
            result = post_responses_grounded(instructions='I', input_text='Q?', key='secret')
        self.assertFalse(result['searched'])
        self.assertEqual(result['citations'], [])

    def test_incomplete_response_is_rejected_never_presented_as_grounded(self):
        from pcb_checker.ai.meta import post_responses_grounded
        cut = self.searched()
        cut['status'] = 'incomplete'
        cut['incomplete_details'] = {'reason': 'max_output_tokens'}
        with patch('pcb_checker.ai.reviewer._request', return_value=cut):
            with self.assertRaises(AIError) as caught:
                post_responses_grounded(instructions='I', input_text='Q?', key='secret')
        self.assertEqual(caught.exception.diagnostics['error_kind'], 'truncated')

    def test_unreadable_or_empty_search_raises_without_key(self):
        from pcb_checker.ai.meta import post_responses_grounded
        with patch('pcb_checker.ai.reviewer._request', return_value=None), self.assertRaises(AIError):
            post_responses_grounded(instructions='I', input_text='Q?', key='secret-key')
        with patch('pcb_checker.ai.reviewer._request', return_value=self.payload([])):
            try:
                post_responses_grounded(instructions='I', input_text='Q?', key='secret-key')
            except AIError as exc:
                self.assertNotIn('secret-key', str(exc))
            else:
                self.fail('empty search output must raise')


class AIErrorResponseTests(unittest.TestCase):
    def test_truncation_reaches_user_with_real_reason(self):
        from pcb_checker.webapp import ai_error_response
        body, status = ai_error_response(AIError('Meta stopped early: the answer exceeded the output limit.',
                                                 diagnostics={'error_kind': 'truncated', 'retryable': False}))
        self.assertEqual(status, 400)
        self.assertIn('output limit', body['error'])
        self.assertFalse(body['retryable'])

    def test_budget_exhaustion_maps_to_429(self):
        from pcb_checker.webapp import ai_error_response
        body, status = ai_error_response(AIError('Too many requests.', diagnostics={'error_kind': 'busy', 'retryable': True}))
        self.assertEqual(status, 429)
        self.assertTrue(body['retryable'])

if __name__=='__main__':unittest.main()
