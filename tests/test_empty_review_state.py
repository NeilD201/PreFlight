"""The schematic-preview warning must name the actual limitation, never blame the whole review."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.schematic_visual import render_schematic
from test_review import fixture


class EmptyPreviewMessageTests(unittest.TestCase):
    def test_missing_schematic_names_the_preview_only(self):
        p = fixture()
        p.selected_files = {}
        with patch('pcb_checker.schematic_visual.find_kicad', return_value='/kicad'):
            meta, assets = render_schematic(p, '/project')
        self.assertEqual(meta['status'], 'unavailable')
        self.assertIn('No schematic file was selected', meta['message'])
        self.assertIn('Other checks are unaffected', meta['message'])
        self.assertNotIn('KiCad CLI', meta['message'])
        self.assertFalse(assets)

    def test_missing_kicad_cli_names_the_cli_only(self):
        p = fixture()
        p.selected_files = {'schematic': 'test.kicad_sch'}
        with patch('pcb_checker.schematic_visual.find_kicad', return_value=None):
            meta, assets = render_schematic(p, '/project')
        self.assertEqual(meta['status'], 'unavailable')
        self.assertIn('KiCad CLI was not found', meta['message'])
        self.assertNotIn('schematic and KiCad CLI', meta['message'])
        self.assertFalse(assets)

    def test_old_global_warning_is_gone(self):
        p = fixture()
        p.selected_files = {}
        with patch('pcb_checker.schematic_visual.find_kicad', return_value=None):
            meta, _ = render_schematic(p, '/project')
        self.assertNotIn('needed for this view', meta['message'])


if __name__ == '__main__':
    unittest.main()
