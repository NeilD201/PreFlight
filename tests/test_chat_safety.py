import hashlib
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppError
from pcb_checker.design_review import context_key
from pcb_checker.chat_safety import evidence_state
from test_design_review import project
import test_webapp


class ChatSafetyTests(unittest.TestCase):
    setUp=test_webapp.StateTests.setUp
    tearDown=test_webapp.StateTests.tearDown

    def prepare(self):
        p=project();self.state.project=p;self.state.report_id='saved'
        self.state.store.save_context(context_key(p),p.design_context,0)
        p.design_context=self.state.store.design_context(context_key(p))
        return p

    def test_credentials_rejected_before_storage_or_provider(self):
        p=self.prepare();self.state.openai_key='configured-secret-key-value'
        with patch('pcb_checker.webapp.answer_project') as provider:
            for secret in ('configured-secret-key-value','sk-proj-'+'a'*30,'AIza'+'b'*35):
                for data in ({'question':secret},{'question':'Hello','history':[{'role':'user','text':secret}]}):
                    with self.assertRaisesRegex(AppError,'API key'):
                        self.state.project_chat({'report_id':'saved',**data})
            provider.assert_not_called()
        self.assertEqual(self.state.store.conversations(context_key(p)),[])
        self.assertEqual(self.state.chat_requests,set())

    def test_concurrent_tabs_do_not_send_twice_and_guard_releases(self):
        self.prepare();entered=threading.Event();release=threading.Event();errors=[]
        def provider(*args,**kwargs):
            entered.set();release.wait(3)
            return {'status':'ai','answer':'Done'}
        request={'report_id':'saved','question':'Tell me about U42'}
        def work():
            try:self.state.project_chat(request)
            except Exception as exc:errors.append(exc)
        with patch('pcb_checker.webapp.answer_project',side_effect=provider) as send:
            thread=threading.Thread(target=work);thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(AppError,'already being prepared') as error:self.state.project_chat(request)
                self.assertEqual(error.exception.status,409)
                self.assertEqual(send.call_count,1)
            finally:release.set();thread.join(4)
        self.assertFalse(errors);self.assertFalse(thread.is_alive());self.assertEqual(self.state.chat_requests,set())
        with patch('pcb_checker.webapp.answer_project',side_effect=RuntimeError('failure')):
            with self.assertRaises(RuntimeError):self.state.project_chat(request)
        self.assertEqual(self.state.chat_requests,set())

    def test_changed_files_reach_model_and_warning_survives_history(self):
        p=self.prepare();file=self.source/'example.kicad_pcb'
        p.provenance['source_hashes']={file.name:'old-digest'}
        self.state.store.preference('input:saved',{'path':str(self.source)})
        def provider(project,*args,**kwargs):
            self.assertEqual(project.provenance['chat_evidence']['status'],'changed')
            return {'status':'ai','answer':'This describes the saved version.'}
        with patch('pcb_checker.webapp.answer_project',side_effect=provider):
            result=self.state.project_chat({'report_id':'saved','question':'How does U42 connect?'})
        self.assertIn('update the review',result['evidence_state']['warning'])
        saved=self.state.store.conversations(context_key(p))[-1]
        self.assertEqual(saved['metadata']['evidence_state']['status'],'changed')

    def test_file_changes_while_answer_runs_are_detected(self):
        p=self.prepare();file=self.source/'example.kicad_pcb'
        p.provenance['source_hashes']={file.name:hashlib.sha256(file.read_bytes()).hexdigest()}
        self.state.store.preference('input:saved',{'path':str(self.source)})
        def provider(project,*args,**kwargs):
            self.assertEqual(project.provenance['chat_evidence']['status'],'current')
            file.write_text(file.read_text()+'\n')
            return {'status':'ai','answer':'Saved version answer'}
        with patch('pcb_checker.webapp.answer_project',side_effect=provider):
            result=self.state.project_chat({'report_id':'saved','question':'Explain the power path'})
        self.assertEqual(result['evidence_state']['status'],'changed')

    def test_unavailable_source_is_not_claimed_current_and_new_context_is_detected(self):
        p=self.prepare()
        self.assertEqual(evidence_state(p,None,1)['status'],'unavailable')
        self.assertEqual(evidence_state(p,'project.zip',1)['status'],'snapshot')
        self.assertTrue(evidence_state(p,'project.zip',1,2)['context_changed'])
