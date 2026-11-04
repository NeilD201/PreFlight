"""Regression coverage for evidence-based value/package sanity checks (priority 3).

USB-C CC pull-downs, LED series resistance, high-CV MLCC plausibility, and
IC decoupling presence. Every rule must stay silent on compliant boards.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.checks.values import value_sanity_findings
from pcb_checker.models import Component, ComponentRecord, Pad, Pin, Project
from pcb_checker.review import build_review


def comp(ref, value="", footprint="", symbol="", pins=(), pads=(), dnp=False):
    records = [ComponentRecord(ref, value, footprint, "s.kicad_sch", 1, dnp=dnp)]
    pcb = [ComponentRecord(ref, value, footprint, "b.kicad_pcb", 2, dnp=dnp,
                           pads=[Pad(n, net, i + 1, "smd", fn) for i, (n, net, fn) in enumerate(pads)])]
    component = Component(ref, records, pcb, [])
    component.schematic_pins = [Pin(number=n, name=name, electrical_type=etype, net=net)
                               for n, name, etype, net in pins]
    return component


def project_with(*components):
    project = Project("values", selected_files={"pcb": "b.kicad_pcb", "schematic": "s.kicad_sch"})
    for component in components:
        project.components[component.reference] = component
    return project


def rules(project):
    return {f["rule"] for f in value_sanity_findings(project)}


class UsbCcTests(unittest.TestCase):
    def board(self, cc_value=None):
        parts = [comp("J1", "USB_C", "Conn_USB_C", "",
                      pins=[("A5", "CC1", "passive", "CC1"), ("B5", "CC2", "passive", "CC2")])]
        if cc_value is not None:
            parts.append(comp("R8", cc_value, "R_0402", "",
                              pads=[("1", "CC1", ""), ("2", "GND", "")]))
            parts.append(comp("R9", cc_value, "R_0402", "",
                              pads=[("1", "CC2", ""), ("2", "GND", "")]))
        return project_with(*parts)

    def test_correct_5k1_is_silent(self):
        self.assertEqual(rules(self.board("5.1k")), set())

    def test_wrong_value_is_flagged(self):
        project = self.board("10k")
        findings = value_sanity_findings(project)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "usb_c_cc_pull_down")
        self.assertIn("10k", findings[0]["summary"])

    def test_missing_resistors_flagged(self):
        findings = value_sanity_findings(self.board(None))
        self.assertEqual(len(findings), 1)
        self.assertIn("no resistor", findings[0]["summary"])


class LedTests(unittest.TestCase):
    def test_dim_series_resistor_flagged(self):
        project = project_with(
            comp("D168", "LED", "LED_0603", "Device:LED", pads=[("1", "LED3", ""), ("2", "GND", "")]),
            comp("R12", "10k", "R_0603", "", pads=[("1", "LED3", ""), ("2", "GPIO26", "")]))
        findings = value_sanity_findings(project)
        self.assertEqual([f["rule"] for f in findings], ["led_series_resistance"])

    def test_sane_series_resistor_silent(self):
        project = project_with(
            comp("D168", "LED", "LED_0603", "Device:LED", pads=[("1", "LED3", ""), ("2", "GND", "")]),
            comp("R12", "500", "R_0603", "", pads=[("1", "LED3", ""), ("2", "GPIO26", "")]))
        self.assertEqual(value_sanity_findings(project), [])

    def test_ground_sharing_pull_down_is_not_series(self):
        # A CC-style pull-down shares GND with every LED VSS pin; that alone
        # must not read as an LED series resistor (false positive seen live).
        project = project_with(
            comp("D1", "SK6812", "LED_SMD:SK6812", "LED:SK6812",
                 pads=[("1", "DIN", ""), ("2", "GND", ""), ("3", "+5V", "")]),
            comp("R2", "10k", "R_0402", "", pads=[("1", "CC1", ""), ("2", "GND", "")]))
        self.assertEqual(rules(project), set())


class MlccTests(unittest.TestCase):
    def test_high_cv_small_package_on_rail_flagged(self):
        project = project_with(
            comp("C105", "100u", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]))
        findings = value_sanity_findings(project)
        self.assertEqual([f["rule"] for f in findings], ["mlcc_package_plausibility"])

    def test_same_value_caps_consolidate_with_rail_context(self):
        project = project_with(
            comp("C105", "100u", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]),
            comp("C106", "100u", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "+3.3V", ""), ("2", "GND", "")]),
            comp("U7", "LD1117", "SOT-223", "",
                 pins=[("1", "GND", "power_in", "GND"), ("2", "VO", "power_out", "+3.3V"),
                       ("3", "VI", "power_in", "+5V")],
                 pads=[("1", "GND", ""), ("2", "+3.3V", ""), ("3", "+5V", "")]))
        findings = value_sanity_findings(project)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertIn("C105", finding["title"])
        self.assertIn("+5V (power pin of U7)", finding["summary"])
        self.assertIn("+3.3V (power pin of U7)", finding["summary"])
        self.assertEqual({t["reference"] for t in finding["chain_targets"]}, {"C105", "C106"})
        self.assertIn("No replacement part number is given", finding["why"])

    def test_gpio_driven_led_gets_screening_current_and_value(self):
        project = project_with(
            comp("D1", "LED", "LED_0603", "",
                 pads=[("1", "GND", ""), ("2", "Net-(R1-A)", "")]),
            comp("R1", "10k", "R_0402", "",
                 pads=[("1", "Net-(U1-GPIO5)", ""), ("2", "Net-(R1-A)", "")]))
        findings = [f for f in value_sanity_findings(project) if f["rule"] == "led_series_resistance"]
        self.assertEqual(len(findings), 1)
        steps = " ".join(findings[0]["steps"])
        self.assertIn("0.13 mA", steps)
        self.assertIn("680", steps)
        self.assertIn("Net-(U1-GPIO5)", steps)

    def test_mlcc_steps_name_per_rail_minimum_rating(self):
        project = project_with(
            comp("C105", "100u", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]))
        findings = [f for f in value_sanity_findings(project) if f["rule"] == "mlcc_package_plausibility"]
        self.assertEqual(len(findings), 1)
        steps = " ".join(findings[0]["steps"])
        self.assertIn("at least 10 V", steps)
        self.assertIn("+5V", steps)

    def test_small_value_or_big_package_silent(self):
        project = project_with(
            comp("C3", "100n", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]),
            comp("C106", "100u", "Capacitor_SMD:C_1210_3225Metric", "",
                 pads=[("1", "+5V", ""), ("2", "GND", "")]),
            comp("C107", "100u", "Capacitor_SMD:C_0402_1005Metric", "",
                 pads=[("1", "Net-(C107-Pad1)", ""), ("2", "GND", "")]))
        self.assertEqual(value_sanity_findings(project), [])


class DecouplingTests(unittest.TestCase):
    def mcu(self, value="RP2040"):
        pins = [(str(n), f"GPIO{n}", "bidirectional", f"GPIO{n}") for n in range(1, 7)]
        pins += [("50", "VDD", "power_in", "VDD"), ("51", "GND", "power_in", "GND")]
        return comp("U2", value, "QFN-56", "", pins=pins)

    def test_bare_ic_without_caps_flagged(self):
        findings = value_sanity_findings(project_with(self.mcu()))
        self.assertEqual([f["rule"] for f in findings], ["ic_decoupling_presence"])

    def test_decoupled_ic_silent(self):
        project = project_with(self.mcu(), comp("C3", "100n", "C_0402", "",
                                                pads=[("1", "VDD", ""), ("2", "GND", "")]))
        self.assertEqual(value_sanity_findings(project), [])

    def test_module_exempt(self):
        findings = value_sanity_findings(project_with(self.mcu("ESP32-WROOM-32")))
        self.assertEqual(findings, [])


class SelectionTests(unittest.TestCase):
    def test_value_findings_reach_vector_selection(self):
        project = project_with(
            comp("J1", "USB_C", "Conn_USB_C", "",
                 pins=[("A5", "CC1", "passive", "CC1"), ("B5", "CC2", "passive", "CC2")]),
            comp("R8", "10k", "R_0402", "", pads=[("1", "CC1", ""), ("2", "GND", "")]),
            comp("R9", "10k", "R_0402", "", pads=[("1", "CC2", ""), ("2", "GND", "")]))
        project.review = build_review(project)
        packet = evidence_packet(project)
        cc_groups = [g for g in project.review["groups"] if "CC resistors" in g["title"]]
        self.assertEqual(len(cc_groups), 1)
        self.assertIn(cc_groups[0]["id"], packet["action_group_ids"])


if __name__ == "__main__":
    unittest.main()
