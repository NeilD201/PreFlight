import json
import stat
import tempfile
import unittest
from pathlib import Path

import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker import file_fixes as fixes
from pcb_checker import fix_workspace as ws


class FixWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / 'project'
        (self.source / 'sub').mkdir(parents=True)
        (self.source / 'board.kicad_pcb').write_bytes(b'(kicad_pcb v1)')
        (self.source / 'board.kicad_sch').write_bytes('schematic \u00e9lan\n'.encode('utf-8'))
        (self.source / 'sub' / 'lib.bin').write_bytes(bytes(range(256)))
        self.root = Path(self.tmp.name) / 'workspace'
        self.rels = ['board.kicad_pcb', 'board.kicad_sch', 'sub/lib.bin']

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self):
        return ws.create_snapshot(self.source, self.rels, self.root)

    def test_snapshot_manifest_round_trip(self):
        manifest = self.snapshot()
        self.assertEqual(set(manifest['files']), set(self.rels))
        for rel in self.rels:
            self.assertEqual(manifest['files'][rel],
                             fixes.digest((self.source / rel).read_bytes()))
        reread = ws.verify_snapshot(self.root, manifest['tx'])
        self.assertEqual(reread['files'], manifest['files'])

    def test_snapshot_copies_are_read_only(self):
        manifest = self.snapshot()
        target = self.root / 'snapshots' / manifest['tx'] / 'original' / 'board.kicad_pcb'
        self.assertFalse(bool(target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)))

    def test_tampered_snapshot_copy_fails_verification(self):
        manifest = self.snapshot()
        target = self.root / 'snapshots' / manifest['tx'] / 'original' / 'board.kicad_pcb'
        target.chmod(0o644)
        target.write_bytes(b'(kicad_pcb TAMPERED)')
        with self.assertRaises(fixes.FixError):
            ws.verify_snapshot(self.root, manifest['tx'])

    def test_source_drift_is_reported_not_applied(self):
        manifest = self.snapshot()
        (self.source / 'board.kicad_pcb').write_bytes(b'(kicad_pcb changed)')
        drift = dict(ws.source_drift(self.source, manifest))
        self.assertEqual(drift['board.kicad_pcb'], 'changed')
        self.assertEqual(drift['board.kicad_sch'], 'ok')
        ws.verify_snapshot(self.root, manifest['tx'])

    def test_candidate_edits_leave_source_and_snapshot_untouched(self):
        manifest = self.snapshot()
        before = {r: fixes.digest((self.source / r).read_bytes()) for r in self.rels}
        cand = ws.create_candidate(self.root, manifest['tx'], label='probe')
        work = Path(cand['work'])
        (work / 'board.kicad_pcb').write_bytes(b'(kicad_pcb candidate)')
        for rel in self.rels:
            self.assertEqual(fixes.digest((self.source / rel).read_bytes()), before[rel])
        ws.verify_snapshot(self.root, manifest['tx'])
        diff = ws.candidate_diff(self.root, cand['candidate'])
        self.assertTrue(diff['board.kicad_pcb']['changed'])
        self.assertIn('candidate', diff['board.kicad_pcb']['diff'])
        self.assertFalse(diff['board.kicad_sch']['changed'])

    def test_rollback_restores_snapshot_content_and_logs(self):
        manifest = self.snapshot()
        cand = ws.create_candidate(self.root, manifest['tx'])
        work = Path(cand['work'])
        (work / 'board.kicad_pcb').write_bytes(b'(kicad_pcb candidate)')
        ws.rollback_candidate(self.root, cand['candidate'])
        self.assertEqual((work / 'board.kicad_pcb').read_bytes(), b'(kicad_pcb v1)')
        self.assertFalse(ws.candidate_diff(self.root, cand['candidate'])['board.kicad_pcb']['changed'])
        events = [json.loads(line)['event'] for line in
                  (self.root / 'candidates' / cand['candidate'] / 'transactions.jsonl').read_text().splitlines()]
        self.assertEqual(events, ['candidate_created', 'rollback_completed'])

    def test_original_preservation_across_full_lifecycle(self):
        before = {r: fixes.digest((self.source / r).read_bytes()) for r in self.rels}
        manifest = self.snapshot()
        cand = ws.create_candidate(self.root, manifest['tx'])
        work = Path(cand['work'])
        for rel in self.rels:
            (work / rel).write_bytes(b'candidate edit')
        ws.rollback_candidate(self.root, cand['candidate'])
        after = {r: fixes.digest((self.source / r).read_bytes()) for r in self.rels}
        self.assertEqual(before, after)

    def test_unsafe_paths_and_missing_files_rejected(self):
        with self.assertRaises(fixes.FixError):
            ws.create_snapshot(self.source, ['../escape.kicad_pcb'], self.root)
        with self.assertRaises(fixes.FixError):
            ws.create_snapshot(self.source, ['absent.kicad_pcb'], self.root)


if __name__ == '__main__':
    unittest.main()
