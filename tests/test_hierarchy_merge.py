"""Regression coverage for hierarchical schematic sheet merging (priority 4).

Child-sheet symbols must join the comparison instead of silently dropping
out; unresolved sheets keep comparison disabled with recorded reasons.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.analysis import _merge_child_sheets, analyze_project
from pcb_checker.models import Issue, Project, ProjectFile
from pcb_checker.parsers.kicad_schematic import child_sheet_files, parse_schematic

TOP = "aaaaaaaa-0000-0000-0000-000000000001"
SHEET = "bbbbbbbb-0000-0000-0000-000000000002"

ROOT_SCH = f"""(kicad_sch (version 20250101) (generator "test") (uuid "{TOP}")
  (paper "A4")
  (sheet (at 10 10) (size 80 50) (uuid "{SHEET}")
    (property "Sheetname" "PSU") (property "Sheetfile" "psu.kicad_sch")
    (pin "5V" output (at 10 10 0) (uuid "cccccccc-0000-0000-0000-000000000003")))
  (symbol (lib_id "Device:R") (at 0 0 0) (unit 1) (uuid "dddddddd-0000-0000-0000-000000000004")
    (property "Reference" "R1") (property "Value" "10k") (property "Footprint" "R_0402")
    (instances (project "proj" (path "/{TOP}" (reference "R1") (unit 1))))))
"""

CHILD_SCH = f"""(kicad_sch (version 20250101) (generator "test") (uuid "{SHEET}")
  (paper "A4")
  (symbol (lib_id "Device:C") (at 0 0 0) (unit 1) (uuid "eeeeeeee-0000-0000-0000-000000000005")
    (property "Reference" "C1") (property "Value" "100n") (property "Footprint" "C_0402")
    (instances (project "proj" (path "/{TOP}/{SHEET}" (reference "C1") (unit 1))))))
"""


def make_project(directory, files):
    project = Project("hier")
    for name in files:
        project.files.append(ProjectFile(name, "schematic", 10))
    return project


class ChildSheetFileTests(unittest.TestCase):
    def test_sheet_filenames_listed(self):
        self.assertEqual(child_sheet_files(ROOT_SCH), ["psu.kicad_sch"])

    def test_no_sheets_empty(self):
        self.assertEqual(child_sheet_files(CHILD_SCH), [])


class ParseChildTests(unittest.TestCase):
    def test_child_instance_resolves_under_top_path(self):
        parsed = parse_schematic(CHILD_SCH, "psu.kicad_sch",
                                 instance_root="/" + TOP, project_name="proj")
        self.assertTrue(parsed.comparison_supported)
        self.assertEqual([(r.reference, r.value) for r in parsed.components], [("C1", "100n")])

    def test_root_without_merge_stays_unsupported(self):
        parsed = parse_schematic(ROOT_SCH, "proj.kicad_sch")
        self.assertFalse(parsed.comparison_supported)
        self.assertTrue(any(i.code == "HIERARCHY_NOT_SUPPORTED" for i in parsed.issues))


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "proj.kicad_sch"
        self.root.write_text(ROOT_SCH)

    def merge(self, extra=None):
        files = {"proj.kicad_sch": ROOT_SCH, **(extra or {})}
        for name, text in files.items():
            (Path(self.tmp.name) / name).write_text(text)
        project = make_project(self.tmp.name, list(files))
        parsed = parse_schematic(ROOT_SCH, "proj.kicad_sch")
        root_item = next(f for f in project.files if f.path == "proj.kicad_sch")
        return project, _merge_child_sheets(project, self.tmp.name, root_item, ROOT_SCH, parsed)

    def test_merge_restores_comparison(self):
        project, parsed = self.merge({"psu.kicad_sch": CHILD_SCH})
        self.assertTrue(parsed.comparison_supported)
        self.assertEqual(sorted(r.reference for r in parsed.components), ["C1", "R1"])
        self.assertTrue(any(i.code == "HIERARCHY_MERGED" for i in parsed.issues))
        self.assertFalse(any(i.code == "HIERARCHY_NOT_SUPPORTED" for i in parsed.issues))

    def test_missing_child_keeps_skip_with_reason(self):
        project, parsed = self.merge()
        self.assertFalse(parsed.comparison_supported)
        self.assertTrue(any(i.code == "HIERARCHY_SHEET_UNRESOLVED" for i in parsed.issues))
        self.assertEqual([r.reference for r in parsed.components], ["R1"])

    def test_duplicate_reference_keeps_skip_with_reason(self):
        dupe = CHILD_SCH.replace('"C1"', '"R1"')
        project, parsed = self.merge({"psu.kicad_sch": dupe})
        self.assertFalse(parsed.comparison_supported)
        self.assertTrue(any(i.code == "HIERARCHY_DUPLICATE_REFERENCE" for i in parsed.issues))


class AnalyzeHierarchyTests(unittest.TestCase):
    def test_analyze_merges_child_sheet_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "proj.kicad_pro").write_text("(kicad_pro)\n")
            Path(directory, "proj.kicad_sch").write_text(ROOT_SCH)
            Path(directory, "psu.kicad_sch").write_text(CHILD_SCH)
            project = analyze_project(directory, native=False)
        self.assertIn("R1", project.components)
        self.assertIn("C1", project.components)
        self.assertTrue(any(i.code == "HIERARCHY_MERGED" for i in project.issues),
                        "child sheets must be merged with a recorded reason")


if __name__ == "__main__":
    unittest.main()
