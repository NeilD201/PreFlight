import json
import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.firmware.generator import generate_firmware
from test_firmware_plan import spec as plan_spec


def gen_spec(**over):
    base = {
        "mcu": {"status": "identified", "reference": "U13", "family": "nRF52",
                "declared_value": "nRF52840", "declared_mpn": "nRF52840-QIAA",
                "package": "QFN-73", "confidence": "high", "evidence": []},
        "power": {"supply_nets": ["VDD"], "ground_nets": ["GND"]},
        "buses": [{"kind": "I2C", "nets": ["SDA"], "members": ["U13", "U11"],
                   "mcu_pins": [], "all_pins": [], "pullups": []}],
        "peripherals": [],
        "gpio_map": [
            {"pin": "29", "name": "P0.29", "schematic_net": "SDA0",
             "pcb_net": "SDA0", "match": True},
            {"pin": "A8", "name": "", "schematic_net": "",
             "pcb_net": "SCL", "match": False}],
        "stated_purpose": "Log temperature.",
        "open_questions": [],
        "limits": [],
    }
    base.update(over)
    return base


def gen_plan(**over):
    base = plan_spec()
    base.update(over)
    return base


class GenerateTests(unittest.TestCase):
    def test_bus_crystal_debug_pins_left_unconfigured(self):
        spec = gen_spec(
            gpio_map=[
                {"pin": "1", "name": "P0.02", "schematic_net": "SDA0",
                 "pcb_net": "SDA0", "match": True},
                {"pin": "2", "name": "P0.03", "schematic_net": "SCL0",
                 "pcb_net": "SCL0", "match": True},
                {"pin": "3", "name": "P0.00", "schematic_net": "XL1",
                 "pcb_net": "XL1", "match": True},
                {"pin": "4", "name": "P0.18", "schematic_net": "SWDIO",
                 "pcb_net": "SWDIO", "match": True},
                {"pin": "5", "name": "P0.13", "schematic_net": "LED0",
                 "pcb_net": "LED0", "match": True},
                {"pin": "6", "name": "P0.20", "schematic_net": "SENSOR SCLK",
                 "pcb_net": "SENSOR SCLK", "match": True},
                {"pin": "7", "name": "P0.24", "schematic_net": "SENSOR SDO",
                 "pcb_net": "SENSOR SDO", "match": True},
                {"pin": "8", "name": "P0.25", "schematic_net": "SENSOR CSB",
                 "pcb_net": "SENSOR CSB", "match": True}],
            buses=[{"kind": "I2C", "nets": ["SDA0", "SCL0"],
                    "members": ["U13"], "mcu_pins": [],
                    "all_pins": [], "pullups": []}])
        plan = {"drivers": [{"reference": "U5", "part": "?", "status": "manual",
                             "basis": {}, "note": "needs datasheet"}],
                "modules": []}
        result = generate_firmware(spec, plan)
        self.assertTrue(result["ok"])
        main = result["files"]["src/main.c"]
        self.assertIn("gpio_output(PIN_P0_13)", main)
        self.assertIn("gpio_output(PIN_P0_25)", main)  # chip select stays GPIO by design
        for macro in ("PIN_P0_02", "PIN_P0_03", "PIN_P0_00", "PIN_P0_18",
                      "PIN_P0_20", "PIN_P0_24"):
            self.assertNotIn("gpio_output(%s)" % macro, main)

    def test_init_results_checked_and_purpose_loop_present(self):
        spec = gen_spec()
        plan = {"drivers": [{"reference": "U5", "part": "?", "status": "manual",
                             "basis": {}, "note": "needs datasheet"}],
                "modules": []}
        result = generate_firmware(spec, plan)
        self.assertTrue(result["ok"])
        main = result["files"]["src/main.c"]
        self.assertIn("(drv_u5_init() == 0)", main)
        self.assertIn("drv_u5_poll()", main)
        drv_h = result["files"]["src/drv_u5.h"]
        self.assertIn("int drv_u5_poll(void);", drv_h)

    def test_nrf52_project_structure(self):
        inner = gen_plan()
        inner["peripherals"] = []
        inner["drivers"] = [
            {"reference": "U11", "part": "MAX30102", "pins": [],
             "driver": "drv_u11", "status": "scaffold",
             "basis": {"interface": "I2C", "source_title": "t", "source_url": "u"},
             "note": ""},
            {"reference": "U9", "part": "BMP390", "pins": [],
             "driver": "drv_u9", "status": "manual", "note": ""},
            {"reference": "U5", "part": "unknown", "pins": [],
             "driver": "drv_u5", "status": "unknown", "note": ""},
        ]
        inner["modules"] = ["hal_gpio", "hal_i2c"]
        result = generate_firmware(gen_spec(), inner)
        self.assertTrue(result["ok"], result.get("reason"))
        files = result["files"]
        for path in ("README.md", "Makefile", "linker/nrf52.ld", "src/vectors.c",
                     "src/main.c", "src/pins.h", "src/soc_nrf52.h",
                     "src/hal_gpio.h", "src/hal_gpio.c",
                     "src/hal_i2c.h", "src/hal_i2c.c",
                     "src/drv_u11.h", "src/drv_u11.c",
                     "src/drv_u9.h", "src/drv_u9.c", "firmware.json"):
            self.assertIn(path, files)
        self.assertNotIn("src/drv_u5.c", files)
        self.assertIn("static const pin_id_t PIN_P0_29 = {0, 29};", files["src/pins.h"])
        self.assertIn("TODO confirm package pin", files["src/pins.h"])
        self.assertIn("(drv_u11_init() == 0)", files["src/main.c"])
        self.assertIn("U5 excluded", files["src/main.c"])
        self.assertIn("arm-none-eabi-gcc", files["Makefile"])
        self.assertIn("src/drv_u11.c", files["Makefile"])
        self.assertNotIn("drv_u5", files["Makefile"])
        manifest = json.loads(files["firmware.json"])
        self.assertEqual(manifest["target"]["declared_value"], "nRF52840")
        self.assertTrue(all(v["status"] in ("pass", "warn") for v in result["validation"]))

    def test_scaffold_without_bus_module_still_emits_hal(self):
        # fxD case: scaffold I2C driver but no bus candidates, so the plan
        # lists only hal_gpio. The build must still resolve every include.
        inner = gen_plan()
        inner["modules"] = ["hal_gpio"]
        inner["drivers"] = [
            {"reference": "U11", "part": "MAX30102", "pins": [],
             "driver": "drv_u11", "status": "scaffold",
             "basis": {"interface": "I2C", "source_title": "t", "source_url": "u"},
             "note": ""},
        ]
        result = generate_firmware(gen_spec(), inner)
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertIn("src/hal_i2c.h", result["files"])
        self.assertIn("src/hal_i2c.c", result["files"])
        self.assertIn("src/hal_i2c.c", result["files"]["Makefile"])
        self.assertIn('#include "hal_i2c.h"', result["files"]["src/main.c"])
        self.assertIn('#include "hal_i2c.h"', result["files"]["src/drv_u11.c"])
        self.assertTrue(any("hal_i2c" in v["detail"] for v in result["validation"]))

    def test_hostile_cad_text_cannot_break_comments(self):
        spec = gen_spec()
        spec["gpio_map"] = [{"pin": "1", "name": "P0.1", "schematic_net": "A */ B",
                             "pcb_net": "A */ B", "match": True}]
        result = generate_firmware(spec, gen_plan())
        pins = result["files"]["src/pins.h"]
        self.assertNotIn("A */ B", pins)
        self.assertIn("A * / B", pins)

    def test_makefile_pattern_rule_is_valid_make(self):
        result = generate_firmware(gen_spec(), gen_plan())
        self.assertIn("build/%.o: src/%.c", result["files"]["Makefile"])
        self.assertNotIn("%%", result["files"]["Makefile"])

    def test_pad_names_map_to_ports(self):
        spec = gen_spec()
        spec["gpio_map"] = [
            {"pin": "A10", "name": "AIN5/P0.29", "schematic_net": "",
             "pcb_net": "SDA", "match": False},
            {"pin": "A8", "name": "AIN4/P0.31", "schematic_net": "",
             "pcb_net": "SCL", "match": False}]
        inner = gen_plan()
        inner["modules"] = ["hal_gpio"]
        inner["drivers"] = []
        result = generate_firmware(spec, inner)
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertIn("static const pin_id_t PIN_AIN5_P0_29 = {0, 29};",
                      result["files"]["src/pins.h"])
        self.assertIn("static const pin_id_t PIN_AIN4_P0_31 = {0, 31};",
                      result["files"]["src/pins.h"])

    def test_non_nrf52_blocked_without_scaffolding(self):
        spec = gen_spec()
        spec["mcu"] = dict(spec["mcu"], family="ESP32", declared_value="ESP32-S3")
        result = generate_firmware(spec, gen_plan())
        self.assertFalse(result["ok"])
        self.assertIn("ESP32", result["reason"])

    def test_unknown_exact_part_blocked(self):
        spec = gen_spec()
        spec["mcu"] = dict(spec["mcu"], declared_value="nRF52810", declared_mpn="nRF52810")
        result = generate_firmware(spec, gen_plan())
        self.assertFalse(result["ok"])
        self.assertIn("linker", result["reason"])

    def test_unidentified_mcu_blocked(self):
        spec = gen_spec()
        spec["mcu"] = {"status": "unidentified", "candidates": []}
        result = generate_firmware(spec, gen_plan())
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
