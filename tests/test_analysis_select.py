"""Regression coverage for schematic auto-select on hierarchical KiCad projects.

Bug A (blind benchmark): with several .kicad_sch sheets the review must pick
the root sheet whose stem matches the board/project stem, and HARD STOP with
no AI spend when the root is ambiguous -- never silently run PCB-only.
"""

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.analysis import _select
from pcb_checker.archive import ArchiveError, import_project


def make_zip(directory, names):
    path = Path(directory) / "project.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, "(kicad_sch (version 20250101))\n")
    return path


class SchematicSelectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def select_schematic(self, names):
        project = import_project(make_zip(self.tmp.name, names))
        return _select(project, "schematic", None)

    def test_hierarchical_project_selects_root_matching_pro_stem(self):
        selected = self.select_schematic([
            "wenne.kicad_pro", "wenne.kicad_pcb", "wenne.kicad_sch",
            "wenne-psu.kicad_sch", "wenne-rf.kicad_sch",
        ])
        self.assertEqual(selected.path, "wenne.kicad_sch")

    def test_board_stem_match_wins_over_unrelated_sheet(self):
        selected = self.select_schematic([
            "proj.kicad_pro", "board.kicad_pcb", "board.kicad_sch",
            "notes.kicad_sch",
        ])
        self.assertEqual(selected.path, "board.kicad_sch")

    def test_ambiguous_roots_raise_without_running_checks(self):
        project = import_project(make_zip(self.tmp.name, [
            "proj.kicad_pro", "board.kicad_pcb", "proj.kicad_sch",
            "board.kicad_sch",
        ]))
        with self.assertRaises(ArchiveError) as ctx:
            _select(project, "schematic", None)
        self.assertIn("No circuit checks have run", str(ctx.exception))

    def test_no_matching_stem_raises_without_running_checks(self):
        project = import_project(make_zip(self.tmp.name, [
            "proj.kicad_pro", "board.kicad_pcb", "a.kicad_sch", "b.kicad_sch",
        ]))
        with self.assertRaises(ArchiveError) as ctx:
            _select(project, "schematic", None)
        self.assertIn("No circuit checks have run", str(ctx.exception))

    def test_single_schematic_still_selected(self):
        selected = self.select_schematic([
            "proj.kicad_pro", "board.kicad_pcb", "only.kicad_sch",
        ])
        self.assertEqual(selected.path, "only.kicad_sch")

    def test_explicit_request_overrides_hierarchy(self):
        project = import_project(make_zip(self.tmp.name, [
            "wenne.kicad_pro", "wenne.kicad_pcb", "wenne.kicad_sch",
            "wenne-psu.kicad_sch",
        ]))
        selected = _select(project, "schematic", "wenne-psu.kicad_sch")
        self.assertEqual(selected.path, "wenne-psu.kicad_sch")


if __name__ == "__main__":
    unittest.main()
