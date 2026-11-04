"""Real file saves, atomic replacement, restart persistence and format boundaries."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.editor_connections import EditorConnections, ConnectionError, fingerprints
from pcb_checker.storage import WorkspaceStore
from pcb_checker.analysis import analyze_project
from pcb_checker.archive import ArchiveError


class EditorConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = WorkspaceStore(self.root/'reports')
        self.manager = EditorConnections(self.store, start=False)
        self.paths = []
        for extension, content in [('.kicad_pro','{}'),('.kicad_sch','(kicad_sch)'),('.kicad_pcb','(kicad_pcb)')]:
            path = self.root/('board'+extension)
            path.write_text(content)
            self.paths.append(str(path))

    def tearDown(self):
        self.manager.close()
        self.temp.cleanup()

    def connect(self):
        return self.manager.connect('kicad', self.paths)

    def test_atomic_save_settles_and_does_not_repeat_revision(self):
        record = self.connect()
        temporary = self.root/'new-board'
        temporary.write_text('(kicad_pcb (version 20250101))')
        temporary.replace(self.paths[-1])
        self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['status'], 'saving')
        self.assertEqual(self.manager.get(record['id'])['revision'], 1)
        self.manager.scan()
        saved = self.manager.get(record['id'])
        self.assertEqual(saved['revision'], 2)
        self.assertEqual(saved['status'], 'watching')
        self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['revision'], 2)
        self.assertEqual(Path(self.paths[-1]).read_text(), '(kicad_pcb (version 20250101))')

    def test_restart_and_changes_while_app_closed(self):
        record = self.connect()
        Path(self.paths[-1]).write_text('(kicad_pcb changed)')
        restarted = EditorConnections(self.store, start=False)
        restarted.scan(); restarted.scan()
        self.assertEqual(restarted.get(record['id'])['revision'], 2)
        self.assertEqual(restarted.get(record['id'])['files'], sorted(self.paths))

    def test_existing_kicad_link_migrates_once_and_stays_disconnected(self):
        self.store.preference('linked_kicad_files', {'files': {Path(p).suffix:p for p in self.paths}})
        self.manager.stop.set();self.manager._watch()
        records = self.manager.listing()['connections']
        self.assertEqual(len(records), 1)
        self.manager.disconnect(records[0]['id'])
        restarted = EditorConnections(self.store, start=False)
        restarted.stop.set();restarted._watch()
        self.assertFalse(restarted.listing()['connections'])

    def test_missing_file_recovers_without_new_connection(self):
        record = self.connect()
        board = Path(self.paths[-1]); board.unlink()
        self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['status'], 'unavailable')
        board.write_text('(kicad_pcb restored)')
        self.manager.scan(); self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['status'], 'watching')
        self.assertEqual(self.manager.get(record['id'])['revision'], 2)

    def test_double_connect_and_disconnect_are_idempotent(self):
        record = self.connect()
        self.assertEqual(self.connect()['id'], record['id'])
        self.assertEqual(len(self.manager.listing()['connections']), 1)
        self.manager.disconnect(record['id']);self.manager.disconnect(record['id'])
        self.assertFalse(EditorConnections(self.store, start=False).listing()['connections'])
        self.assertTrue(all(Path(p).exists() for p in self.paths))

    def test_wrong_format_bad_path_symlink_and_empty_file(self):
        for editor, paths in [('other',self.paths),('eagle',self.paths),('kicad',self.paths[:1]),('kicad',['relative.kicad_sch'])]:
            with self.assertRaises(ConnectionError):self.manager.connect(editor, paths)
        link = self.root/'link.kicad_pcb';link.symlink_to(self.paths[-1])
        with self.assertRaises(ConnectionError):self.manager.connect('kicad',self.paths[:2]+[str(link)])
        Path(self.paths[-1]).write_text('')
        with self.assertRaises(ConnectionError):self.connect()

    def test_native_monitoring_never_claims_review_support(self):
        source = self.root/'radio.brd';source.write_text('<?xml version="1.0"?><eagle><drawing><board/></drawing></eagle>')
        record = self.manager.connect('eagle',[str(source)])
        listing = self.manager.listing()
        self.assertFalse(listing['editors']['eagle']['review_available'])
        source.write_text('not eagle XML')
        with self.assertRaises(ConnectionError):self.manager.connect('eagle',[str(source)])
        self.assertEqual(record['revision'], 1)

    def test_failed_database_write_is_retried(self):
        record = self.connect()
        Path(self.paths[-1]).write_text('(kicad_pcb changed)');self.manager.scan()
        with patch.object(self.manager, '_persist', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertRaises(sqlite3.OperationalError):self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['revision'],1)
        self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['revision'],2)

    def test_touch_without_content_change_does_not_invalidate(self):
        record = self.connect()
        Path(self.paths[-1]).touch()
        self.manager.scan();self.manager.scan()
        self.assertEqual(self.manager.get(record['id'])['revision'],1)

    def test_file_changing_during_read_is_not_accepted(self):
        from pcb_checker import editor_connections
        original = editor_connections.metadata
        calls = [original(self.paths), [(0,0,0,0,0)]]
        with patch.object(editor_connections, 'metadata', side_effect=calls):
            with self.assertRaises(ConnectionError):fingerprints(self.paths)

    def test_foreign_project_cannot_produce_empty_successful_review(self):
        folder=self.root/'foreign';folder.mkdir()
        (folder/'board.PcbDoc').write_bytes(b'example native board')
        with self.assertRaisesRegex(ArchiveError, 'cannot analyze yet'):
            analyze_project(folder)


if __name__ == '__main__':
    unittest.main()
