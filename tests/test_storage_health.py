"""Preferences must survive transient storage outages; low disk must warn loudly."""
import io
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.storage import WorkspaceStore
from pcb_checker import webapp, launch_app


class PreferenceCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = WorkspaceStore(Path(self.temp.name))

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_overwrite(self):
        self.assertIsNone(self.store.preference('missing'))
        self.assertEqual(self.store.preference('mode', 'economy'), 'economy')
        self.assertEqual(self.store.preference('mode'), 'economy')
        self.assertEqual(self.store.preference('mode', 'detailed'), 'detailed')
        self.assertEqual(self.store.preference('mode'), 'detailed')
        self.assertEqual(self.store.preference('linked', {'path': '/tmp/x'}), {'path': '/tmp/x'})

    def test_cached_values_do_not_alias_caller_objects(self):
        original = {'path': '/tmp/x'}
        self.store.preference('linked', original)
        original['path'] = '/tmp/mutated'
        self.assertEqual(self.store.preference('linked'), {'path': '/tmp/x'})
        fetched = self.store.preference('linked')
        fetched['path'] = '/tmp/mutated'
        self.assertEqual(self.store.preference('linked'), {'path': '/tmp/x'})

    def test_cached_preference_survives_database_outage(self):
        self.store.preference('linked_kicad_files', {'path': '/tmp/x'})
        self.assertEqual(self.store.preference('linked_kicad_files'), {'path': '/tmp/x'})
        self.store.path.write_bytes(b'not a database')
        self.assertEqual(self.store.preference('linked_kicad_files'), {'path': '/tmp/x'})
        with self.assertRaises(sqlite3.Error):
            self.store.preference('never_cached_key')

    def test_chat_learned_explanations_stay_coherent(self):
        self.assertIsNone(self.store.preference('explanation:proj'))
        self.store.save_conversation('proj', 'r1', 'general', 'q1', 'Explain more simply please')
        with sqlite3.connect(self.store.path) as db:
            raw = db.execute(
                'SELECT value FROM preferences WHERE key=?', ('explanation:proj',)).fetchone()[0]
        import json
        self.assertEqual(self.store.preference('explanation:proj'), json.loads(raw))
        self.assertEqual(self.store.preference('explanation:proj')['mode'], 'plain')


class DiskWarningTests(unittest.TestCase):
    def test_warns_when_volume_nearly_full(self):
        usage = lambda path: type('U', (), {'free': 1024 ** 3})()
        with patch.object(webapp.shutil, 'disk_usage', usage):
            message = webapp.disk_warning(Path('/tmp'))
        self.assertIn('1.0', message)
        self.assertIn('disk space', message.lower())

    def test_quiet_when_space_healthy(self):
        usage = lambda path: type('U', (), {'free': 100 * 1024 ** 3})()
        with patch.object(webapp.shutil, 'disk_usage', usage):
            self.assertEqual(webapp.disk_warning(Path('/tmp')), '')

    def test_quiet_when_volume_unreadable(self):
        with patch.object(webapp.shutil, 'disk_usage', side_effect=OSError('denied')):
            self.assertEqual(webapp.disk_warning(Path('/tmp')), '')

    def test_launcher_warns_but_still_launches(self):
        usage = lambda path: type('U', (), {'free': 1024 ** 3})()
        with patch.object(launch_app.shutil, 'disk_usage', usage), \
                patch.object(launch_app, 'probe', return_value='ready'), \
                patch.object(launch_app.webbrowser, 'open') as browser:
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(launch_app.launch(open_browser=True), 0)
            browser.assert_called_once_with('http://127.0.0.1:8765')
        self.assertIn('disk space', out.getvalue().lower())


if __name__ == '__main__':
    unittest.main()
