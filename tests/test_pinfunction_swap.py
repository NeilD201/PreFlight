"""Regression coverage for the PCB-only pin-function/net-name swap check (Bug C).

A QSPI-style swap (QSPI_SCLK on net Q_SEL mirrored by QSPI_SS on net Q_CLK)
must surface as a review-grade finding from drawn copper alone, without a
schematic or datasheet. Consistent assignments, generic GPIO names, and
single-sided mismatches must stay silent.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.checks.circuit import _signal_key, pcb_pinfunction_swap_findings
from pcb_checker.models import Component, ComponentRecord, Pad, Pin, Project
from pcb_checker.review import build_review


def component(ref, pad_specs):
    pads = [Pad(number, net, index + 1, "smd", fn)
            for index, (number, net, fn) in enumerate(pad_specs)]
    record = ComponentRecord(ref, "part", "pkg:fp", "board.kicad_pcb", 1, pads=pads)
    return Component(ref, [], [record], [])


def project_with(*components):
    project = Project("swap", selected_files={"pcb": "board.kicad_pcb"})
    for comp in components:
        project.components[comp.reference] = comp
    return project


class SignalKeyTests(unittest.TestCase):
    def test_qspi_names_resolve_to_matching_roles(self):
        self.assertEqual(_signal_key("QSPI_SCLK"), "SPI_CLK")
        self.assertEqual(_signal_key("Q_CLK"), "SPI_CLK")
        self.assertEqual(_signal_key("QSPI_SS"), "SPI_SEL")
        self.assertEqual(_signal_key("Q_SEL"), "SPI_SEL")

    def test_generic_and_power_names_are_unclassifiable(self):
        for text in ("GPIO26", "ADC0", "GND", "+5V", "Net-(U1-Pad3)", "FLASH", ""):
            self.assertEqual(_signal_key(text), "", text)


class SwapFindingTests(unittest.TestCase):
    def test_mutual_cross_is_flagged_for_both_ends(self):
        project = project_with(
            component("U1", [("52", "Q_SEL", "QSPI_SCLK")]),
            component("U2", [("56", "Q_CLK", "QSPI_SS")]),
        )
        findings = pcb_pinfunction_swap_findings(project)
        by_ref = {f["reference"]: f for f in findings}
        self.assertEqual(set(by_ref), {"U1", "U2"})
        for finding in findings:
            self.assertEqual(finding["category"], "electrical")
            self.assertEqual(finding["priority"], "review")
            self.assertIn("Q_SEL", finding["summary"])
            self.assertIn("Q_CLK", finding["summary"])

    def test_consistent_assignment_stays_silent(self):
        project = project_with(
            component("U1", [("52", "Q_CLK", "QSPI_SCLK")]),
            component("U2", [("56", "Q_SEL", "QSPI_SS")]),
        )
        self.assertEqual(pcb_pinfunction_swap_findings(project), [])

    def test_single_sided_mismatch_is_not_reported(self):
        project = project_with(component("U1", [("52", "Q_SEL", "QSPI_SCLK")]))
        self.assertEqual(pcb_pinfunction_swap_findings(project), [])

    def test_generic_gpio_mismatch_stays_silent(self):
        project = project_with(
            component("U1", [("1", "LED3", "GPIO26")]),
            component("U2", [("2", "BTN", "GPIO27")]),
        )
        self.assertEqual(pcb_pinfunction_swap_findings(project), [])

    def test_inversion_markers_resolve_to_roles(self):
        self.assertEqual(_signal_key("~{CS}"), "SPI_SEL")
        self.assertEqual(_signal_key("~RESET"), "")

    def make_board(self, u2_clk_net, u2_sel_net):
        """Controller U2 with clock/select on the given nets; flash U5 correct."""
        u2 = Component("U2", [], [ComponentRecord("U2", "RP2040", "pkg:qfn", "b.kicad_pcb", 1, pads=[
            Pad("52", u2_clk_net, 1, "smd", "QSPI_SCLK"), Pad("56", u2_sel_net, 2, "smd", "QSPI_SS")])],
            [Pin("52", "QSPI_SCLK"), Pin("56", "QSPI_SS")])
        u5 = Component("U5", [], [ComponentRecord("U5", "W25Q128", "pkg:soic", "b.kicad_pcb", 2, pads=[
            Pad("1", "Q_SEL", 3, "smd", "~{CS}"), Pad("6", "Q_CLK", 4, "smd", "CLK")])],
            [Pin("1", "~{CS}"), Pin("6", "CLK")])
        project = project_with(u2, u5)
        project.schematic_nets = {"Q_CLK": ["U2.56", "U5.6"], "Q_SEL": ["U2.52", "U5.1"]}
        if u2_clk_net == "Q_CLK":
            project.schematic_nets = {"Q_CLK": ["U2.52", "U5.6"], "Q_SEL": ["U2.56", "U5.1"]}
        return project

    def test_fault_injected_swap_is_confirmed_with_rewire(self):
        project = self.make_board("Q_SEL", "Q_CLK")
        findings = pcb_pinfunction_swap_findings(project)
        confirmed = [f for f in findings if f["reference"] == "U2"]
        self.assertEqual(len(confirmed), 1)
        finding = confirmed[0]
        self.assertIn("confirmed swapped", finding["title"])
        self.assertIn("U2 pin 56 (QSPI_SS) from Q_CLK to Q_SEL", finding["steps"][0])
        self.assertIn("U2 pin 52 (QSPI_SCLK) from Q_SEL to Q_CLK", finding["steps"][0])
        self.assertIn("no datasheet was consulted", finding["evidence"][-1])

    def test_valid_variant_stays_silent(self):
        project = self.make_board("Q_CLK", "Q_SEL")
        self.assertEqual(pcb_pinfunction_swap_findings(project), [])

    def test_swap_without_schematic_stays_possible(self):
        project = project_with(
            component("U1", [("52", "Q_SEL", "QSPI_SCLK")]),
            component("U2", [("56", "Q_CLK", "QSPI_SS")]),
        )
        findings = pcb_pinfunction_swap_findings(project)
        self.assertTrue(all("possible swapped" in f["title"] for f in findings))

    def test_swap_reaches_review_groups_and_vector_selection(self):
        from pcb_checker.ai.reviewer import evidence_packet
        project = project_with(
            component("U1", [("52", "Q_SEL", "QSPI_SCLK")]),
            component("U2", [("56", "Q_CLK", "QSPI_SS")]),
        )
        project.review = build_review(project)
        swap = [g for g in project.review["groups"] if "swapped" in g["title"]]
        # Same-cause CLK/SEL swaps merge into one group preserving both refs.
        self.assertEqual(len(swap), 1)
        self.assertEqual(sorted(swap[0]["components"]), ["U1", "U2"])
        packet = evidence_packet(project)
        selected = set(packet["action_group_ids"])
        self.assertTrue(all(g["id"] in selected for g in swap),
                        "swap findings must be selectable for the AI review")


if __name__ == "__main__":
    unittest.main()
