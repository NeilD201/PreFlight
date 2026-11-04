"""Regression coverage for finding routing (priorities 1, 2, 5).

- Net-named copper stubs escalate with or without a schematic.
- Unconnected-pin notes touching a live finding promote to review.
- Audit targets lead with evidence-implicated references.
- The 40-group prompt bound records overflow instead of dropping it.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.ai.circuit_audit import targets
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.models import Component, ComponentRecord, Issue, Pin, Project
from pcb_checker.review import build_review


def sch_only(ref, pins=()):
    comp = Component(ref, [ComponentRecord(ref, "part", "pkg:fp", "s.kicad_sch", 1)], [], [])
    comp.schematic_pins = [Pin(number=n, name=name, electrical_type=etype, net=net)
                           for n, name, etype, net in pins]
    return comp


def reviewed(project):
    project.review = build_review(project)
    return project


class StubBothModesTests(unittest.TestCase):
    def dangling(self):
        return Issue("warning", "KICAD_DRC_TRACK_DANGLING", "Track has unconnected end",
                     source="kicad_drc",
                     items=[{"description": "Track [Q_CLK] on F.Cu"}],
                     evidence=["Track [Q_CLK] on F.Cu"])

    def test_pcb_only_stub_escalates(self):
        project = reviewed(Project("a", selected_files={"pcb": "b.kicad_pcb"},
                                   issues=[self.dangling()]))
        group = next(g for g in project.review["groups"] if "stub on" in g["title"])
        self.assertEqual((group["category"], group["priority"]), ("electrical", "review"))
        self.assertIn("No schematic is loaded", group["why"])

    def test_schematic_loaded_stub_still_escalates(self):
        project = reviewed(Project("b", selected_files={"pcb": "b.kicad_pcb",
                                                        "schematic": "s.kicad_sch"},
                                   issues=[self.dangling()]))
        group = next(g for g in project.review["groups"] if "stub on" in g["title"])
        self.assertEqual((group["category"], group["priority"]), ("electrical", "review"))
        self.assertIn("schematic confirms", group["why"])


class PinPromotionTests(unittest.TestCase):
    def project(self):
        project = Project("p", selected_files={"pcb": "b.kicad_pcb", "schematic": "s.kicad_sch"})
        project.components["F1"] = sch_only("F1", [("1", "", "passive", "VCC")])
        project.components["U9"] = sch_only("U9", [("1", "", "passive", "OTHER")])
        project.issues = [
            Issue("warning", "SCHEMATIC_COMPONENT_MISSING_FROM_PCB", "F1 has no PCB footprint",
                  component="F1", evidence=["s.kicad_sch:10"]),
            Issue("warning", "KICAD_ERC_PIN_NOT_CONNECTED", "Pin not connected",
                  source="kicad_erc",
                  items=[{"description": "Symbol F1 Pin 1 [Passive]"}],
                  evidence=["Symbol F1 Pin 1 [Passive]"]),
            Issue("warning", "KICAD_ERC_PIN_NOT_CONNECTED", "Pin not connected",
                  source="kicad_erc",
                  items=[{"description": "Symbol U9 Pin 1 [Passive]"}],
                  evidence=["Symbol U9 Pin 1 [Passive]"]),
        ]
        return reviewed(project)

    def test_implicated_pins_promote(self):
        project = self.project()
        groups = {tuple(sorted(
            i.get("description", "") for issue in
            [project.issues[int(fid[1:]) - 1] for fid in g["issue_ids"]] for i in issue.items)): g
            for g in project.review["groups"] if g["category"] in ("electrical", "documentation")}
        f1_pins = next(g for key, g in groups.items()
                       if any("Symbol F1" in text for text in key))
        self.assertEqual(f1_pins["category"], "electrical")
        packet = evidence_packet(project)
        self.assertIn(f1_pins["id"], packet["action_group_ids"])

    def test_unrelated_pins_stay_notes(self):
        project = Project("q", selected_files={"pcb": "b.kicad_pcb", "schematic": "s.kicad_sch"})
        project.components["U9"] = sch_only("U9", [("1", "", "passive", "OTHER")])
        project.issues = [Issue("warning", "KICAD_ERC_NOCONNECT_CONNECTED", "No-connect connected",
                                source="kicad_erc",
                                items=[{"description": "Symbol U9 Pin 1 [Passive]"}],
                                evidence=["Symbol U9 Pin 1 [Passive]"])]
        reviewed(project)
        u9_pins = next(g for g in project.review["groups"]
                       if any("Symbol U9" in i.get("description", "")
                              for fid in g["issue_ids"]
                              for i in project.issues[int(fid[1:]) - 1].items))
        self.assertEqual(u9_pins["category"], "documentation")


class TargetAndOverflowTests(unittest.TestCase):
    def test_implicated_refs_lead_audit_targets(self):
        project = Project("t", selected_files={"pcb": "b.kicad_pcb"})
        project.components["F1"] = sch_only("F1")
        project.issues = [Issue("warning", "SCHEMATIC_COMPONENT_MISSING_FROM_PCB",
                                "F1 has no PCB footprint", component="F1")]
        reviewed(project)
        packet = evidence_packet(project)
        f1_group = next(g for g in project.review["groups"] if g["components"] == ["F1"])
        self.assertIn(f1_group["id"], packet["action_group_ids"])
        self.assertEqual(targets(packet)[0], "F1")

    def test_overflow_is_recorded_not_dropped(self):
        project = Project("o", selected_files={"pcb": "b.kicad_pcb"})
        for index in range(41):
            project.issues.append(Issue(
                "error", f"FAKE_DRC_{index:02d}", f"fabricated finding {index}",
                source="kicad_drc", evidence=[f"item {index}"]))
        reviewed(project)
        packet = evidence_packet(project)
        self.assertEqual(len(packet["action_group_ids"]), 40)
        self.assertEqual(len(packet["selection_overflow"]), 1)
        self.assertIn("reason", packet["selection_overflow"][0])


if __name__ == "__main__":
    unittest.main()
