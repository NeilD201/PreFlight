"""Exercise complete save publication without requiring Fusion in CI."""
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.connectors.fusion.PreflightSync.PreflightSync import export_pair
from pcb_checker.editor_connections import EditorConnections
from pcb_checker.storage import WorkspaceStore


class ExportManager:
    def __init__(self, fail=False):self.fail=fail
    def createEagleBrdExportOptions(self,path):return path
    def createEagleSchExportOptions(self,path):return path
    def execute(self,path):
        if self.fail:return False
        Path(path).write_text('<eagle><drawing>'+str(Path(path).suffix)+'</drawing></eagle>')
        return True


class FusionSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.manager=EditorConnections(WorkspaceStore(self.root/'reports'),start=False)
        self.record=self.manager.prepare_fusion();self.sync=Path(self.record['sync_folder'])
        self.board=SimpleNamespace(name='Test board',parentDocument=SimpleNamespace(isModified=False),exportManager=ExportManager())
        self.sch=SimpleNamespace(parentDocument=SimpleNamespace(isModified=False),exportManager=ExportManager())
    def tearDown(self):self.temp.cleanup()
    def publish(self):
        export_pair(self.sync,self.board,self.sch,'test-design')
        (self.sync/'status.json').write_text(json.dumps({'status':'watching','message':'Saved'}))
        self.manager.scan()
    def test_setup_idempotency_and_complete_snapshot(self):
        self.assertEqual(self.manager.prepare_fusion()['id'],self.record['id'])
        self.assertTrue((Path(self.record['addon_folder'])/'config.json').is_file())
        self.publish();r=self.manager.get(self.record['id'])
        self.assertEqual(r['revision'],1);self.assertEqual(len(r['files']),2)
        self.assertEqual(r['status'],'watching')
        self.manager.scan();self.assertEqual(self.manager.get(r['id'])['revision'],1)
    def test_failed_second_export_does_not_replace_previous_snapshot(self):
        self.publish();before=(self.sync/'current.json').read_bytes()
        self.sch.exportManager.fail=True
        with self.assertRaises(ValueError):export_pair(self.sync,self.board,self.sch,'test-design')
        self.assertEqual(before,(self.sync/'current.json').read_bytes())
        self.assertFalse(list(self.sync.glob('draft-*')))
    def test_dirty_pair_and_changed_identity_are_rejected(self):
        self.board.parentDocument.isModified=True
        with self.assertRaises(ValueError):self.publish()
        self.assertFalse((self.sync/'current.json').exists())
        self.board.parentDocument.isModified=False;self.publish()
        export_pair(self.sync,self.board,self.sch,'another-project');self.manager.scan()
        self.assertEqual(self.manager.get(self.record['id'])['revision'],1)
        self.assertEqual(self.manager.get(self.record['id'])['status'],'unavailable')
    def test_export_retention_and_disconnect(self):
        for i in range(4):self.publish()
        self.assertEqual(len(list(self.sync.glob('save-*'))),2)
        self.manager.disconnect(self.record['id'])
        self.assertFalse(json.loads((self.sync/'control.json').read_text())['enabled'])
        self.assertTrue((self.sync/'current.json').exists())
    def test_traversal_manifest_cannot_read_arbitrary_files(self):
        (self.sync/'current.json').write_text(json.dumps({'generation':'../../elsewhere','identity':'test','files':['design.sch','design.brd']}))
        self.manager.scan();r=self.manager.get(self.record['id'])
        self.assertEqual(r['status'],'unavailable');self.assertEqual(r['files'],[])


if __name__=='__main__':unittest.main()
