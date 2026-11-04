"""Credential persistence keeps secrets outside HTTP configuration and files."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.webapp import AppState,AppError
from pcb_checker.credential_store import KeychainError,account_for,META_SERVICE


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.saved={};self.services={}
        saved=self.saved;services=self.services
        class Store:
            def __init__(self,account,service=None):self.account=account;services[account]=service
            def load(self):return saved.get(self.account,'')
            def save(self,value):saved[self.account]=value
            def delete(self):saved.pop(self.account,None)
        self.patch=patch('pcb_checker.webapp.Keychain',Store);self.patch.start();self.addCleanup(self.patch.stop)
        self.state=AppState(self.folder,self.folder/'reports');self.addCleanup(self.state.close)

    def test_saved_key_survives_restart_without_entering_http_or_workspace_files(self):
        config=self.state.settings({'key':'unit-test-key','remember_key':True})
        self.assertTrue(config['key_saved']);self.assertNotIn('unit-test-key',json.dumps(config))
        second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
        if second.keychain_thread:second.keychain_thread.join(2)
        self.assertEqual(second.key,'unit-test-key');self.assertTrue(second.ai_default)
        self.assertTrue(second.config()['key_saved'])
        for path in (self.folder/'reports').rglob('*'):
            if path.is_file():self.assertNotIn(b'unit-test-key',path.read_bytes())

    def test_session_key_is_not_persisted(self):
        self.state.settings({'key':'session-test-key','remember_key':False})
        self.assertFalse(self.saved)
        second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
        self.assertFalse(second.config()['key_configured'])

    def test_unchecking_removes_saved_copy_but_keeps_current_session(self):
        self.state.settings({'key':'unit-test-key','remember_key':True})
        self.state.settings({'remember_key':False})
        self.assertEqual(self.state.key,'unit-test-key');self.assertFalse(self.saved)
        self.assertFalse(self.state.config()['key_saved'])

    def test_forget_removes_saved_and_session_key(self):
        self.state.settings({'key':'unit-test-key','remember_key':True})
        self.state.settings({'clear_key':True})
        self.assertFalse(self.saved);self.assertFalse(self.state.config()['key_configured'])
        second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
        self.assertFalse(second.key)

    def test_keychain_failure_does_not_fall_back_to_plaintext(self):
        with patch('pcb_checker.webapp.Keychain',side_effect=KeychainError('Keychain is locked')):
            with self.assertRaises(AppError):self.state.settings({'key':'unit-test-key','remember_key':True})
        self.assertFalse(self.state.key);self.assertFalse(self.saved)
        self.assertFalse(self.state.store.preference('remember_gemini_key'))

    def test_saved_model_is_restored(self):
        self.state.settings({'model':'gemini-test-model','key':'unit-test-key','remember_key':True})
        second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
        self.assertEqual(second.model,'gemini-test-model')

    def test_slow_keychain_does_not_block_app_initialization(self):
        started=threading.Event();release=threading.Event()
        class SlowKeychain:
            def __init__(self,*args):pass
            def load(self):
                started.set();release.wait(3);return ''
        self.state.store.preference('remember_gemini_key',True)
        with patch('pcb_checker.webapp.Keychain',SlowKeychain):
            started_at=time.monotonic()
            second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
            self.assertLess(time.monotonic()-started_at,1)
            self.assertTrue(started.wait(1))
            self.assertTrue(second.config()['keychain_loading'])
            release.set()
            second.keychain_thread.join(1)
            self.assertFalse(second.config()['keychain_loading'])

    def test_accounts_are_stable_and_separate_between_workspaces(self):
        self.assertEqual(account_for(self.folder/'reports'),account_for(self.folder/'reports'))
        self.assertNotEqual(account_for(self.folder/'reports'),account_for(self.folder/'other'))

    def test_denied_service_does_not_discard_working_keys(self):
        self.state.store.preference('remember_gemini_key',True)
        self.state.store.preference('remember_meta_key',True)
        saved={account_for(self.folder/'reports'):'gemini-key'}
        class Flaky:
            def __init__(self,account,service=None):self.account=account;self.service=service
            def load(self):
                if self.service==META_SERVICE:raise KeychainError('Meta prompt denied')
                return saved.get(self.account,'')
        with patch('pcb_checker.webapp.Keychain',Flaky):
            second=AppState(self.folder,self.folder/'reports');self.addCleanup(second.close)
            if second.keychain_thread:second.keychain_thread.join(5)
        self.assertEqual(second.key,'gemini-key')
        self.assertFalse(second.meta_key)
        self.assertIn('denied',second.config()['key_storage_error'])
        self.assertFalse(second.config()['keychain_loading'])

if __name__=='__main__':unittest.main()
