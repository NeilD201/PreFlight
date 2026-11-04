"""Regression coverage for copper-stub escalation to Vector (Bug B).

A dangling track or isolated copper island on a designer-named net must reach
the AI review when no schematic is loaded to confirm intent. Without
escalation these classify as documentation-grade and Vector never sees them.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.models import Issue, Project
from pcb_checker.review import _issue_net_names, build_review


def dangling_issue(net="[Q_CLK]"):
    return Issue("warning", "KICAD_DRC_TRACK_DANGLING", "Track has unconnected end",
                 source="kicad_drc",
                 items=[{"description": f"Track {net} on F.Cu", "uuid": "stub-1"}],
                 evidence=[f"Track {net} on F.Cu from (1, 1) to (2, 2)"])


def reviewed(project):
    project.review = build_review(project)
    return project


def stub_groups(project):
    return [g for g in project.review["groups"] if "stub on" in g["title"]]


class StubEscalationTests(unittest.TestCase):
    def test_pcb_only_named_stub_is_electrical_review(self):
        project = reviewed(Project("stub", selected_files={"pcb": "b.kicad_pcb"},
                                   issues=[dangling_issue()]))
        groups = stub_groups(project)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["category"], "electrical")
        self.assertEqual(groups[0]["priority"], "review")
        self.assertIn("Q_CLK", groups[0]["title"])

    def test_pcb_only_named_stub_reaches_vector_selection(self):
        project = reviewed(Project("stub", selected_files={"pcb": "b.kicad_pcb"},
                                   issues=[dangling_issue()]))
        packet = evidence_packet(project)
        selected = set(packet["action_group_ids"])
        self.assertTrue(any(g["id"] in selected for g in stub_groups(project)),
                        "escalated stub must be selectable for the AI review")

    def test_isolated_copper_escalates_the_same_way(self):
        issue = Issue("warning", "KICAD_DRC_ISOLATED_COPPER", "Isolated copper",
                      source="kicad_drc",
                      items=[{"description": "Copper island [Q_SEL] on B.Cu"}],
                      evidence=["Copper island [Q_SEL] on B.Cu"])
        project = reviewed(Project("iso", selected_files={"pcb": "b.kicad_pcb"},
                                   issues=[issue]))
        groups = stub_groups(project)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["category"], "electrical")

    def test_schematic_loaded_stub_still_escalates(self):
        project = reviewed(Project("doc", issues=[dangling_issue()],
                                   selected_files={"pcb": "b.kicad_pcb",
                                                   "schematic": "b.kicad_sch"}))
        groups = stub_groups(project)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["category"], "electrical")
        self.assertIn("schematic confirms", groups[0]["why"])

    def test_auto_generated_net_names_do_not_escalate(self):
        project = reviewed(Project("auto", selected_files={"pcb": "b.kicad_pcb"},
                                   issues=[dangling_issue(net="[Net-(U1-Pad3)]")]))
        self.assertEqual(stub_groups(project), [])

    def test_net_name_extraction_ignores_auto_names(self):
        issue = dangling_issue(net="[Net-(U1-Pad3)]")
        self.assertEqual(_issue_net_names(issue), [])
        self.assertEqual(_issue_net_names(dangling_issue()), ["Q_CLK"])


if __name__ == "__main__":
    unittest.main()
