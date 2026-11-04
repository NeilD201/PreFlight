"""LiveScan storage: projects, validation, images. No network, no AI."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.breadboard_to_PCB import livescan


def blank(pid):
    return {'version': 1, 'id': pid, 'name': 'n', 'created': '', 'updated': '',
            'breadboard': {'size': 'full', 'railGaps': {}},
            'intent': {'text': '', 'fields': {}, 'updated': None},
            'components': [], 'wires': [], 'images': [], 'questions': [],
            'confirmations': {}, 'counters': {}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reports = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip_and_listing(self):
        pid = livescan.new_id()
        livescan.save_project(self.reports, pid, blank(pid))
        self.assertEqual(livescan.load_project(self.reports, pid)['id'], pid)
        listed = livescan.list_projects(self.reports)
        self.assertEqual([p['id'] for p in listed], [pid])
        livescan.delete_project(self.reports, pid)
        self.assertEqual(livescan.list_projects(self.reports), [])
        with self.assertRaises(ValueError):
            livescan.load_project(self.reports, pid)

    def test_validation_rejects_bad_documents(self):
        pid = 'ls-abc123'
        bad = blank(pid)
        bad['version'] = 99
        with self.assertRaises(ValueError):
            livescan.save_project(self.reports, pid, bad)
        bad = blank(pid)
        bad['breadboard']['size'] = 'mega'
        with self.assertRaises(ValueError):
            livescan.save_project(self.reports, pid, bad)
        bad = blank('other')
        with self.assertRaises(ValueError):
            livescan.save_project(self.reports, pid, bad)
        with self.assertRaises(ValueError):
            livescan.save_project(self.reports, '../escape', blank('../escape'))

    def test_images_are_namespaced_and_typed(self):
        pid = livescan.new_id()
        livescan.save_project(self.reports, pid, blank(pid))
        body = b'\x89PNG\r\n\x1a\n' + b'0' * 100
        first = livescan.save_image(self.reports, pid, 'shot.png', body)
        self.assertEqual(first['name'], 'shot.png')
        second = livescan.save_image(self.reports, pid, 'shot.png', body)
        self.assertNotEqual(second['name'], 'shot.png')  # never silently overwrite
        data, mime = livescan.load_image(self.reports, pid, first['name'])
        self.assertEqual((data, mime), (body, 'image/png'))
        with self.assertRaises(ValueError):
            livescan.save_image(self.reports, pid, 'evil.exe', body)
        with self.assertRaises(ValueError):
            livescan.save_image(self.reports, pid, '../x.png', body)
        with self.assertRaises(ValueError):
            livescan.load_image(self.reports, pid, 'missing.png')
        with self.assertRaises(ValueError):
            livescan.save_image(self.reports, pid, 'big.png', b'')
        with self.assertRaises(ValueError):
            livescan.save_image(self.reports, 'ls-nope', 'a.png', body)


if __name__ == '__main__':
    unittest.main()
