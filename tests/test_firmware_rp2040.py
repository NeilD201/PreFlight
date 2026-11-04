"""RP2040 firmware generation and build coverage.

Pico SDK C projects from discovery specs: GPIO mapping, reserved-net
exclusion, I2C bus matrix, manual-driver honesty, and (when a Pico SDK is
available) a real cmake build producing a valid UF2.
"""

import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.firmware import generator as gen
from pcb_checker.firmware.compile import verify_build

UF2_FAMILY_RP2040 = 0xE48BFF56


def rp_spec(**over):
    base = {
        "mcu": {"status": "identified", "reference": "U2", "family": "RP2040",
                "declared_value": "RP2040", "declared_mpn": "",
                "package": "QFN-56", "confidence": "medium", "evidence": []},
        "power": {"supply_nets": ["+3.3V"], "ground_nets": ["GND"]},
        "buses": [],
        "peripherals": [],
        "gpio_map": [
            {"pin": "1", "name": "GPIO0", "schematic_net": "SDA",
             "pcb_net": "SDA", "match": True},
            {"pin": "2", "name": "GPIO1", "schematic_net": "SCL",
             "pcb_net": "SCL", "match": True},
            {"pin": "30", "name": "GPIO26", "schematic_net": "LED_A",
             "pcb_net": "LED_A", "match": True},
            {"pin": "99", "name": "", "schematic_net": "",
             "pcb_net": "XIN", "match": False}],
        "stated_purpose": "Blink the status LED.",
        "open_questions": [],
        "limits": [],
    }
    base.update(over)
    return base


def rp_plan(**over):
    base = {"framework": "Pico SDK C", "toolchain": "arm-none-eabi-gcc",
            "drivers": [{"reference": "U5", "part": "?", "status": "manual",
                         "basis": {}, "note": "needs datasheet"}],
            "modules": []}
    base.update(over)
    return base


class Rp2040GenerateTests(unittest.TestCase):
    def test_gpio_numbers_map_and_package_pins_stay_placeholders(self):
        result = gen.generate_firmware(rp_spec(), rp_plan())
        self.assertTrue(result["ok"], result.get("reason"))
        pins_h = result["files"]["pins.h"]
        self.assertIn("#define PIN_GPIO0 0", pins_h)
        self.assertIn("#define PIN_GPIO1 1", pins_h)
        self.assertIn("#define PIN_GPIO26 26", pins_h)
        self.assertIn("TODO confirm package pin", pins_h)
        self.assertEqual(result["stats"]["placeholders"], 1)

    def test_mismatched_gpio_row_becomes_placeholder_and_warns(self):
        rows = [dict(r) for r in rp_spec()["gpio_map"]]
        rows[2]["match"] = False  # GPIO26: schematic and PCB disagree
        result = gen.generate_firmware(rp_spec(gpio_map=rows), rp_plan())
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertNotIn("#define PIN_GPIO26", result["files"]["pins.h"])
        self.assertIn("TODO confirm package pin PIN_GPIO26", result["files"]["pins.h"])
        self.assertNotIn("gpio_init(PIN_GPIO26)", result["files"]["main.c"])
        self.assertEqual(result["stats"]["placeholders"], 2)
        pin_map = next(v for v in result["validation"] if v["check"] == "pin_map")
        self.assertEqual(pin_map["status"], "warn")
        self.assertIn("GPIO26", pin_map["detail"])

    def test_gpio_suffix_names_map_to_numbers(self):
        rows = [dict(r) for r in rp_spec()["gpio_map"]]
        rows.append({"pin": "29", "name": "GPIO29_ADC3", "schematic_net": "VSYS",
                     "pcb_net": "VSYS", "match": True})
        result = gen.generate_firmware(rp_spec(gpio_map=rows), rp_plan())
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertIn("#define PIN_GPIO29_ADC3 29", result["files"]["pins.h"])

    def test_agreed_unconnected_gpio_stays_unconfigured(self):
        rows = [dict(r) for r in rp_spec()["gpio_map"]]
        rows.append({"pin": "30", "name": "GPIO25", "schematic_net": "unconnected-(U2-30)",
                     "pcb_net": "unconnected-(U2-30)", "match": True})
        result = gen.generate_firmware(rp_spec(gpio_map=rows), rp_plan())
        self.assertTrue(result["ok"], result.get("reason"))
        self.assertIn("#define PIN_GPIO25 25", result["files"]["pins.h"])
        self.assertNotIn("gpio_init(PIN_GPIO25)", result["files"]["main.c"])
        self.assertIn("agree it is unconnected", result["files"]["main.c"])

    def test_bus_nets_left_unconfigured_outputs_driven(self):
        result = gen.generate_firmware(
            rp_spec(buses=[{"kind": "I2C", "nets": ["SDA", "SCL"],
                            "members": ["U2"], "mcu_pins": [],
                            "all_pins": [], "pullups": []}]),
            rp_plan())
        main = result["files"]["main.c"]
        self.assertIn("left unconfigured: bus, crystal, or debug net", main)
        self.assertNotIn("gpio_init(PIN_GPIO0)", main)
        self.assertNotIn("gpio_init(PIN_GPIO1)", main)
        self.assertIn("gpio_set_dir(PIN_GPIO26, GPIO_OUT)", main)

    def test_i2c_bus_matrix_drives_hal_only_on_matrix_pairs(self):
        spec = rp_spec(buses=[{"kind": "I2C", "nets": ["SDA", "SCL"],
                               "members": ["U2"], "mcu_pins": [],
                               "all_pins": [], "pullups": []}])
        result = gen.generate_firmware(spec, rp_plan())
        # GPIO0/GPIO1 is an i2c0 pair: HAL files exist and init the bus.
        self.assertIn("hal_i2c.c", result["files"])
        self.assertIn("i2c_init(i2c0", result["files"]["hal_i2c.c"])
        self.assertIn("hardware_i2c", result["files"]["CMakeLists.txt"])

    def test_unmappable_bus_produces_no_hal(self):
        spec = rp_spec(
            gpio_map=[{"pin": "1", "name": "GPIO0", "schematic_net": "SDA",
                       "pcb_net": "SDA", "match": True},
                      {"pin": "2", "name": "GPIO2", "schematic_net": "SCL",
                       "pcb_net": "SCL", "match": True}],
            buses=[{"kind": "I2C", "nets": ["SDA", "SCL"],
                    "members": ["U2"], "mcu_pins": [],
                    "all_pins": [], "pullups": []}])
        result = gen.generate_firmware(spec, rp_plan())
        self.assertNotIn("hal_i2c.c", result["files"])
        i2c = next(v for v in result["validation"] if v["check"] == "i2c")
        self.assertEqual(i2c["status"], "warn")

    def test_protocol_drivers_excluded_from_build_and_listed(self):
        result = gen.generate_firmware(rp_spec(), rp_plan())
        self.assertNotIn("src/drv_u5.c", result["files"])
        self.assertNotIn("drv_u5", result["files"]["CMakeLists.txt"])
        self.assertIn("U5", result["files"]["README.md"])
        self.assertIn("excluded from this bring-up build", result["files"]["main.c"])

    def test_other_families_still_blocked(self):
        spec = rp_spec(mcu={"status": "identified", "reference": "U9",
                            "family": "ESP32", "declared_value": "ESP32",
                            "confidence": "high"})
        result = gen.generate_firmware(spec, rp_plan())
        self.assertFalse(result["ok"])
        self.assertIn("RP2040", result["reason"])


class Rp2040BuildTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("PICO_SDK_PATH")
                         and Path(os.environ["PICO_SDK_PATH"]).is_dir(),
                         "Pico SDK checkout required for the real RP2040 build")
    def test_real_sdk_build_produces_valid_uf2(self):
        result = gen.generate_firmware(rp_spec(), rp_plan())
        self.assertTrue(result["ok"])
        report = verify_build(result["files"], timeout=600)
        self.assertEqual(report["status"], "COMPILED", report.get("reason"))
        sizes = report.get("sizes", {})
        self.assertGreater(sizes.get("elf_bytes", 0), 0)
        self.assertGreater(sizes.get("uf2_bytes", 0), 0)
        # The temp dir is gone; rebuild the UF2 check from a kept copy.
        import tempfile
        from pcb_checker.firmware.compile import _write_project, _sizes
        with tempfile.TemporaryDirectory(prefix="rp2040-uf2-") as tmp:
            _write_project(result["files"], tmp)
            from pcb_checker.firmware.compile import _run_cmake
            built = _run_cmake(tmp, 600)
            self.assertTrue(built["ok"], built.get("reason"))
            uf2 = Path(tmp) / "build" / "firmware.uf2"
            data = uf2.read_bytes()
            self.assertGreater(len(data), 0)
            self.assertEqual(len(data) % 512, 0)
            magic0, magic1 = struct.unpack("<II", data[:8])
            family, = struct.unpack("<I", data[28:32])
            magic_end, = struct.unpack("<I", data[508:512])
            self.assertEqual(magic0, 0x0A324655)
            self.assertEqual(magic1, 0x9E5D5157)
            self.assertEqual(family, UF2_FAMILY_RP2040)
            self.assertEqual(magic_end, 0x0AB16F30)


if __name__ == "__main__":
    unittest.main()
