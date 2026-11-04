import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.firmware.build_spec import build_firmware_spec
from pcb_checker.firmware.questions import route_questions, extras_from_answers
from test_firmware_spec import project_dict, mcu_component, ic, pin


class QuestionRoutingTests(unittest.TestCase):
    def test_identity_questions_go_to_the_user(self):
        spec = build_firmware_spec(project_dict(mcu_component(), ic(), purpose="Log"))
        routed = {q["resolves"]: q for q in route_questions(spec)}
        peripheral = routed["peripheral"]
        self.assertEqual(peripheral["status"], "user")
        self.assertIn("U2", peripheral["question"])
        self.assertTrue(peripheral["hint"])
        self.assertTrue(peripheral["decision_id"].startswith("qc-"))
        self.assertEqual(peripheral["scope"]["components"], ["U2"])
        # The mcu is identified, so no mcu question was produced.
        self.assertNotIn("mcu", routed)

    def test_unidentified_mcu_is_asked_not_delegated(self):
        plain = {"reference": "U1", "schematic": [], "pcb": [],
                 "schematic_pins": [pin("1", "VDD", "VDD")], "bom": [], "cpl": []}
        spec = build_firmware_spec(project_dict(plain, purpose="Log"))
        routed = {q["resolves"]: q for q in route_questions(spec)}
        self.assertEqual(routed["mcu"]["status"], "user")

    def test_connectivity_gap_delegates_to_investigation(self):
        spec = build_firmware_spec(project_dict(mcu_component(), circuit_status="unavailable",
                                                purpose="Log"))
        routed = {q["resolves"]: q for q in route_questions(spec)}
        self.assertEqual(routed["connectivity"]["status"], "delegated")
        self.assertEqual(routed["connectivity"]["target_agent"], "investigation")

    def test_prior_answers_resolve_without_asking(self):
        spec = build_firmware_spec(project_dict(mcu_component(), purpose=""))
        prior = {"What should the firmware do?": "Log temperature hourly."}
        routed = {q["resolves"]: q for q in route_questions(spec, prior_answers=prior)}
        self.assertEqual(routed["purpose"]["status"], "resolved")
        self.assertIn("Log temperature hourly", routed["purpose"]["fact"])

    def test_pin_facts_never_answer_identity(self):
        # The packet knows U2's nets; that must not count as knowing what U2 is.
        spec = build_firmware_spec(project_dict(mcu_component(), ic(), purpose="Log"))
        routed = {q["resolves"]: q for q in route_questions(spec)}
        self.assertEqual(routed["peripheral"]["status"], "user")

    def test_extras_backfill_identity_and_purpose(self):
        extras = extras_from_answers([
            {"need": "What is component U2?", "resolves": "peripheral",
             "scope": {"components": ["U2"], "nets": []},
             "answer": "Optical sensor", "mpn": "MAX30001"},
            {"need": "What should the firmware do?", "resolves": "purpose",
             "scope": {}, "answer": "Measure SpO2."}])
        self.assertEqual(extras["identities"]["U2"]["mpn"], "MAX30001")
        self.assertEqual(extras["purpose"], "Measure SpO2.")
        family = extras_from_answers([
            {"need": "What are the 2 parts?", "resolves": "peripheral",
             "scope": {"components": ["P3", "P4"], "nets": []},
             "answer": "I2C hub", "mpn": "TCA9548A"}])
        self.assertEqual(family["identities"]["P3"]["mpn"], "TCA9548A")
        self.assertEqual(family["identities"]["P4"]["mpn"], "TCA9548A")
        spec = build_firmware_spec(project_dict(mcu_component(), ic(), purpose=""), extras)
        self.assertEqual(spec["stated_purpose"], "Measure SpO2.")
        self.assertFalse([q for q in spec["open_questions"] if q["resolves"] == "purpose"])


if __name__ == "__main__":
    unittest.main()
