"""KiCad discovery must work on Windows, macOS and Linux without real installs."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _pcb_bootstrap  # noqa: F401
from pcb_checker.native import find_kicad


class DiscoveryTests(unittest.TestCase):
    def test_explicit_path_passes_through_which(self):
        with patch('pcb_checker.native.shutil.which', return_value='/custom/kicad-cli') as which:
            self.assertEqual(find_kicad('/custom/kicad-cli'), '/custom/kicad-cli')
            which.assert_called_once_with('/custom/kicad-cli')

    def test_path_hit_wins_over_conventional_locations(self):
        with patch('pcb_checker.native.shutil.which', return_value='/usr/bin/kicad-cli'):
            self.assertEqual(find_kicad(), '/usr/bin/kicad-cli')

    def test_windows_conventional_location(self):
        def fake_which(name):
            return None

        def fake_isfile(path):
            # Isolate from the machine running the test: only the Windows
            # 9.0 location exists, nothing else (no macOS bundle, no PATH).
            return isinstance(path, str) and path.startswith('C:\\Program Files\\KiCad\\9.0')

        with patch('pcb_checker.native.shutil.which', side_effect=fake_which), \
             patch('pcb_checker.native.sys.platform', 'win32'), \
             patch('pcb_checker.native.os.path.isfile', side_effect=fake_isfile):
            found = find_kicad()
            self.assertTrue(found.endswith('kicad-cli.exe'))
            self.assertIn('9.0', found)

    def test_missing_kicad_returns_none(self):
        with patch('pcb_checker.native.shutil.which', return_value=None), \
             patch('pcb_checker.native.os.path.isfile', return_value=False):
            self.assertIsNone(find_kicad())
