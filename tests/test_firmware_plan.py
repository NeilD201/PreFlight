import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.firmware.plan import build_firmware_plan, FRAMEWORKS


def spec(mcu_status="identified", family="nRF52", purpose="Log temperature",
         open_questions=(), buses=(), peripherals=()):
    return {
        "mcu": {"status": mcu_status, "reference": "U1", "family": family,
                "declared_value": "nRF52840", "declared_mpn": "nRF52840-QIAA",
                "package": "QFN-73", "confidence": "high", "evidence": ["Declared MPN"]},
        "power": {"supply_nets": ["VDD"], "ground_nets": ["GND"]},
        "buses": list(buses),
        "peripherals": list(peripherals),
        "gpio_map": [{"pin": "29", "name": "P0.29", "schematic_net": "SDA0",
                      "pcb_net": "SDA0", "match": True}],
        "stated_purpose": purpose,
        "open_questions": list(open_questions),
        "limits": ["Pin assignments come from the reviewed nets."],
    }


class PlanTests(unittest.TestCase):
    def test_nrf52_selects_bare_metal_with_local_toolchain(self):
        result = build_firmware_plan(spec())
        self.assertTrue(result["ok"])
        plan = result["plan"]
        self.assertEqual(plan["framework"], "Bare-metal C (ARM Cortex-M)")
        self.assertEqual(plan["toolchain"]["binary"], "arm-none-eabi-gcc")
        self.assertTrue(plan["toolchain"]["available"])
        self.assertIn("hal_gpio", plan["modules"])
        self.assertEqual(plan["target"]["reference"], "U1")
        self.assertEqual(plan["gpio_count"], 1)
        self.assertTrue(plan["pin_map"])

    def test_i2c_bus_adds_hal_and_scaffold_driver(self):
        result = build_firmware_plan(spec(
            buses=[{"kind": "I2C", "nets": ["SDA0"], "members": ["U1", "U2"],
                    "mcu_pins": [{"pin": "29"}], "all_pins": [], "pullups": []}],
            peripherals=[{"reference": "U2", "value": "SHT41", "mpn": "SHT41-AD1B",
                          "manufacturer": "Sensirion", "part_rule": "",
                          "package": "", "pins": [], "evidence": []}]))
        plan = result["plan"]
        self.assertIn("hal_i2c", plan["modules"])
        self.assertEqual(plan["drivers"][0]["status"], "manual")

    def test_esp32_selects_idf_but_flags_missing_toolchain(self):
        inner = spec(family="ESP32")
        inner["mcu"].update(declared_value="ESP32-S3", declared_mpn="ESP32-S3")
        result = build_firmware_plan(inner, env={})
        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"]["framework"], "ESP-IDF")
        self.assertFalse(result["plan"]["toolchain"]["available"])
        self.assertTrue(any("not installed" in limit for limit in result["plan"]["limitations"]))

    def test_missing_purpose_becomes_a_decision(self):
        result = build_firmware_plan(spec(purpose=""))
        needs = [d["need"] for d in result["plan"]["decisions"]]
        self.assertIn("What should the firmware do?", needs)

    def test_discovery_questions_carry_into_decisions(self):
        result = build_firmware_plan(spec(open_questions=[
            {"need": "Which rail powers the sensor?", "why": "Two rails reach it.", "resolves": "power"}]))
        self.assertTrue(any(d["source"] == "discovery" for d in result["plan"]["decisions"]))

    def test_saved_answers_mark_decisions_answered(self):
        asked = "What should turning the encoders SW1, SW2 do?"
        result = build_firmware_plan(
            spec(open_questions=[{"need": asked, "why": "Intent.", "resolves": "encoder_behavior"}]),
            answers=[{"need": asked, "answer": "Volume keys."}])
        decisions = [d for d in result["plan"]["decisions"] if d["need"] == asked]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["source"], "answered")
        self.assertEqual(decisions[0]["answer"], "Volume keys.")
        result = build_firmware_plan(
            spec(open_questions=[{"need": asked, "why": "Intent.", "resolves": "encoder_behavior"}]))
        self.assertEqual(result["plan"]["decisions"][0]["source"], "discovery")

    def test_encoder_default_accepted_and_behavior_recognized(self):
        asked = "What should turning the encoder E1 do?"
        question = {"need": asked, "why": "Intent.", "resolves": "encoder_behavior",
                    "suggested_default": "Volume up/down on rotation; mute on press (common keyboard default, change freely).",
                    "scope": {"components": ["E1"], "nets": []}}
        result = build_firmware_plan(
            spec(open_questions=[question]),
            answers=[{"need": asked, "answer": "default"}])
        decisions = [d for d in result["plan"]["decisions"] if d["need"] == asked]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["source"], "answered_default")
        self.assertIn("Volume up/down", decisions[0]["answer"])
        behavior = result["plan"]["recognized_behavior"]
        self.assertTrue(any("Rotary encoder" in b and "E1" in b for b in behavior),
                        "identified encoder wiring must be recognized, not re-asked")

    def test_unidentified_mcu_blocks_planning(self):
        result = build_firmware_plan(spec(mcu_status="unidentified"))
        self.assertFalse(result["ok"])
        self.assertIn("MCU", result["reason"])

    def test_unknown_identity_yields_unknown_driver(self):
        inner = spec()
        inner["peripherals"] = [{"reference": "U2", "value": "MAX30001", "mpn": "",
                                 "manufacturer": "", "part_rule": "", "package": "",
                                 "pins": [], "evidence": [], "identified": False}]
        result = build_firmware_plan(inner)
        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"]["drivers"][0]["status"], "unknown")

    def test_selection_basis_cites_evidence(self):
        result = build_firmware_plan(spec(
            buses=[{"kind": "I2C", "nets": ["SDA0"], "members": [], "mcu_pins": [],
                    "all_pins": [], "pullups": []}],
            purpose="Log temperature"))
        basis = result["plan"]["selection_basis"]
        self.assertIn("nRF52", basis)
        self.assertIn("arm-none-eabi-gcc", basis)
        self.assertIn("I2C", basis)

    def test_ambiguous_mcu_names_candidates(self):
        inner = spec(mcu_status="ambiguous")
        inner["mcu"]["candidates"] = [{"reference": "U1"}, {"reference": "U2"}]
        result = build_firmware_plan(inner)
        self.assertFalse(result["ok"])
        self.assertIn("U1", result["reason"])
        self.assertIn("U2", result["reason"])

    def test_scaffold_driver_cites_real_datasheet(self):
        inner = spec()
        inner["peripherals"] = [{"reference": "U2", "value": "MAX30102", "mpn": "",
                                 "manufacturer": "", "part_rule": "max30102",
                                 "package": "", "pins": [], "evidence": [],
                                 "identified": True}]
        driver = build_firmware_plan(inner)["plan"]["drivers"][0]
        self.assertEqual(driver["status"], "scaffold")
        self.assertIn("analog.com", driver["basis"]["source_url"])
        self.assertEqual(driver["basis"]["interface"], "I2C")

    def test_meta_review_annotates_without_blocking(self):
        import json as _json
        from pcb_checker.firmware.plan import review_plan_with_meta
        base = build_firmware_plan(spec())["plan"]
        fake = {"steps": [{"content": [{"text": _json.dumps(
            {"framework_agreement": True,
             "suggestions": [{"area": "power", "text": "Add sleep modes"}],
             "risks": ["Unidentified parts"]})}]}],
                "usage": {"total_tokens": 10}}
        out = review_plan_with_meta(dict(base), spec(), key="k",
                                    post=lambda *a, **k: fake)
        self.assertEqual(out["meta_review"]["status"], "completed")
        self.assertTrue(out["meta_review"]["framework_agreement"])
        self.assertEqual(out["meta_review"]["suggestions"][0]["area"], "power")
        self.assertNotIn("meta_review", base)

    def test_meta_review_sees_chains_and_identities(self):
        import json as _json
        from pcb_checker.firmware.plan import review_plan_with_meta
        base = build_firmware_plan(spec())["plan"]
        inner = spec()
        inner["led_chains"] = [{"head": "D1", "count": 3, "verified": True,
                                "mcu_net": "RGB", "mcu_pin": "4 GPIO2",
                                "driver": "U8", "bridges": ["R10"]}]
        inner["peripherals"] = [{"reference": "U8", "value": "SN74LV1T34DBV",
                                 "mpn": "", "manufacturer": "",
                                 "part_rule": "level_shifter", "package": "",
                                 "pins": [], "evidence": [], "identified": True}]
        seen = {}
        def fake(payload, key, **kwargs):
            seen.update(payload)
            return {"steps": [{"content": [{"text": _json.dumps(
                {"framework_agreement": True, "suggestions": [], "risks": []})}]}],
                    "usage": {}}
        review_plan_with_meta(dict(base), inner, key="k", post=fake)
        sent = _json.loads(seen["input"])
        self.assertEqual(sent["led_chains"],
                         [{"head": "D1", "count": 3, "verified": True,
                           "mcu_net": "RGB", "mcu_pin": "4 GPIO2",
                           "driver": "U8", "series_resistors": 1}])
        self.assertIn({"reference": "U8", "part": "SN74LV1T34DBV",
                       "identified": True}, sent["peripheral_identities"])

    def test_meta_review_unavailable_without_key_or_on_failure(self):
        from pcb_checker.firmware.plan import review_plan_with_meta
        base = build_firmware_plan(spec())["plan"]
        off = review_plan_with_meta(dict(base), spec(), key="")
        self.assertEqual(off["meta_review"]["status"], "unavailable")
        def boom(*a, **k):
            raise ValueError("provider down")
        failed = review_plan_with_meta(dict(base), spec(), key="k", post=boom)
        self.assertEqual(failed["meta_review"]["status"], "unavailable")
        self.assertNotIn("meta_review", base)

    def test_unsupported_family_blocks_planning(self):
        result = build_firmware_plan(spec(family="Z80"))
        self.assertFalse(result["ok"])
        self.assertIn("Z80", result["reason"])


if __name__ == "__main__":
    unittest.main()
