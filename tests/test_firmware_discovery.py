"""Firmware discovery: no interrogation about already-known hardware.

Addressable LEDs group into chains, regulators/crystals/connectors/flash
never generate identity questions, and remaining unknowns ask once per
identical group instead of once per component.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.firmware.build_spec import build_firmware_spec


def pin(number, name, net):
    return {"number": number, "name": name, "net": net}


def part(ref, value, footprint="", pins=()):
    return {ref: {
        "schematic": [{"value": value, "footprint": footprint, "symbol": "",
                       "source_file": "t.kicad_sch", "line": 1, "dnp": False}],
        "pcb": [{"value": value, "footprint": footprint,
                 "source_file": "t.kicad_pcb", "line": 1, "dnp": False,
                 "pads": [{"number": n, "name": nm, "net": net} for n, nm, net in pins]}],
        "schematic_pins": [{"number": n, "name": nm, "net": net,
                            "electrical_type": "passive"} for n, nm, net in pins]}}


def project(*parts, purpose="Do stuff"):
    components = {}
    for part_dict in parts:
        components.update(part_dict)
    identities = {ref: {"value": (c["schematic"][0]["value"] if c["schematic"] else "")}
                  for ref, c in components.items()}
    return {"components": components,
            "circuit": {"status": "available", "identities": identities,
                        "supply_nets": [], "bus_candidates": []},
            "schematic_nets": {}}, {"purpose": purpose}


def needs(spec):
    return [q["need"] for q in spec["open_questions"]]


class LedChainTests(unittest.TestCase):
    def test_chained_leds_ask_no_identity_questions(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "LED_IN")]),
            part("D100", "SK6812MINI", "SK6812MINI",
                 [("1", "VDD", "VDD"), ("2", "DOUT", "LED_1"),
                  ("3", "GND", "GND"), ("4", "DIN", "LED_IN")]),
            part("D101", "SK6812MINI", "SK6812MINI",
                 [("1", "VDD", "VDD"), ("2", "DOUT", "LED_2"),
                  ("3", "GND", "GND"), ("4", "DIN", "LED_1")]),
            part("D102", "SK6812MINI", "SK6812MINI",
                 [("1", "VDD", "VDD"), ("2", "DOUT", "LED_OUT"),
                  ("3", "GND", "GND"), ("4", "DIN", "LED_2")]))
        spec = build_firmware_spec(proj, extras)
        self.assertEqual([q for q in needs(spec) if "D100" in q or "D101" in q], [])
        self.assertEqual(len(spec["led_chains"]), 1)
        chain = spec["led_chains"][0]
        self.assertEqual(chain["leds"], ["D100", "D101", "D102"])
        self.assertTrue(chain["verified"])
        self.assertEqual(chain["mcu_net"], "LED_IN")

    def test_broken_chain_marks_unresolved(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "LED_IN")]),
            part("D100", "SK6812MINI", "SK6812MINI",
                 [("1", "VDD", "VDD"), ("2", "DOUT", "LED_1"),
                  ("3", "GND", "GND"), ("4", "DIN", "LED_IN")]),
            part("D101", "SK6812MINI", "SK6812MINI",
                 [("1", "VDD", "VDD"), ("2", "DOUT", "LED_2"),
                  ("3", "GND", "GND"), ("4", "DIN", "NOWHERE")]))
        spec = build_firmware_spec(proj, extras)
        self.assertFalse(all(c["verified"] for c in spec["led_chains"]))
        self.assertEqual([q for q in needs(spec) if "D10" in q], [])


class KnownKindTests(unittest.TestCase):
    def test_support_parts_never_asked(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "N1")]),
            part("U3", "LD1117V", "SOT-223",
                 [("1", "GND", "GND"), ("2", "VO", "V3R3"), ("4", "VI", "V5")]),
            part("Y1", "12MHz", "Crystal", [("1", "X1", "X1"), ("2", "X2", "X2")]),
            part("J1", "USB_C", "Conn_USB_C",
                 [("A6", "DP", "USB_DP"), ("A7", "DM", "USB_DN")]),
            part("U4", "W25Q128", "SOIC-8",
                 [("1", "CS", "FLASH_CS"), ("2", "DO", "FLASH_DO"),
                  ("5", "DI", "FLASH_DI"), ("6", "CLK", "FLASH_CLK")]),
            part("R1", "10k", "R_0402", [("1", "N1", "N1"), ("2", "N2", "N2")]))
        spec = build_firmware_spec(proj, extras)
        for ref in ("U3", "Y1", "J1", "U4", "R1"):
            self.assertEqual([q for q in needs(spec) if ref in q], [],
                             "no identity question for %s" % ref)
        kinds = {p["reference"]: p["part_rule"] for p in spec["peripherals"]}
        self.assertEqual(kinds["U4"], "spi_flash")


class FusedNameTests(unittest.TestCase):
    def test_fused_encoder_and_crystal_names_never_asked(self):
        # Board-realistic spellings: fused RotaryEncoder value, Crystal
        # footprint-style value without an explicit MHz rating.
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "N1")]),
            part("SW1", "RotaryEncoder_Switch", "RotaryEncoder",
                 [("1", "A", "Net-(SW1-A)"), ("2", "B", "Net-(SW1-B)"),
                  ("3", "C", "GND")]),
            part("SW2", "RotaryEncoder_Switch", "RotaryEncoder",
                 [("1", "A", "Net-(SW2-A)"), ("2", "B", "Net-(SW2-B)"),
                  ("3", "C", "GND")]),
            part("Y1", "Crystal_GND24", "Crystal_SMD",
                 [("1", "X1", "XIN"), ("2", "X2", "XOUT"),
                  ("3", "GND", "GND")]))
        spec = build_firmware_spec(proj, extras)
        identity_qs = [q for q in needs(spec)
                       if "may need a firmware driver" in q]
        self.assertEqual(identity_qs, [])
        # Encoders get exactly one grouped behavior question, never one
        # identity question per encoder.
        encoder_qs = [q for q in spec["open_questions"]
                      if q.get("resolves") == "encoder_behavior"]
        self.assertEqual(len(encoder_qs), 1)
        self.assertIn("SW1", encoder_qs[0]["need"])
        self.assertIn("SW2", encoder_qs[0]["need"])
        self.assertEqual([q for q in needs(spec) if "Y1" in q], [])
        rules = {p["reference"]: p.get("part_rule")
                 for p in spec["peripherals"]}
        self.assertEqual(rules.get("SW1"), "rotary_encoder")
        self.assertEqual(rules.get("Y1"), "crystal_or_resonator")


class ChainBridgeTests(unittest.TestCase):
    def test_series_resistor_merges_chain_and_names_mcu_pin(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "MCU_DIN")]),
            part("D1", "SK6812MINI", "SK6812MINI",
                 [("4", "DIN", "MCU_DIN"), ("2", "DOUT", "MID_A")]),
            part("R9", "51R", "R_0402",
                 [("1", "~", "MID_A"), ("2", "~", "MID_B")]),
            part("D2", "SK6812MINI", "SK6812MINI",
                 [("4", "DIN", "MID_B"), ("2", "DOUT", "END")]))
        spec = build_firmware_spec(proj, extras)
        self.assertEqual(len(spec["led_chains"]), 1)
        chain = spec["led_chains"][0]
        self.assertEqual(chain["leds"], ["D1", "D2"])
        self.assertEqual(chain["bridges"], ["R9"])
        self.assertTrue(chain["verified"])
        self.assertEqual(chain["mcu_net"], "MCU_DIN")
        self.assertIn("GPIO1", chain["mcu_pin"])

    def test_head_through_resistor_and_buffer_verifies_driver(self):
        # U8-style: MCU GPIO -> level-shifter -> series R -> LED chain.
        # The MCU ground pin shares GND with the shifter: supply pins must
        # not count as the MCU-side signal.
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("4", "GPIO2", "RGB"),
                                            ("5", "GND", "GND")]),
            part("U8", "SN74LV1T34DBV", "SOT-23-5",
                 [("2", "A", "RGB"), ("4", "Y", "LEDIN"),
                  ("5", "VCC", "+5V"), ("3", "GND", "GND")]),
            part("R10", "51R", "R_0402",
                 [("1", "~", "LEDIN"), ("2", "~", "HEADDIN")]),
            part("D1", "SK6812MINI", "SK6812MINI",
                 [("4", "DIN", "HEADDIN"), ("2", "DOUT", "END")]))
        spec = build_firmware_spec(proj, extras)
        chain = spec["led_chains"][0]
        self.assertEqual(chain["leds"], ["D1"])
        self.assertTrue(chain["verified"], chain)
        self.assertEqual(chain["driver"], "U8")
        self.assertEqual(chain["mcu_net"], "RGB")
        self.assertIn("GPIO2", chain["mcu_pin"])
        self.assertEqual(chain["source_path"], ["R10"])

    def test_undriven_head_stays_unverified(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "N1")]),
            part("D1", "SK6812MINI", "SK6812MINI",
                 [("4", "DIN", "FLOATING"), ("2", "DOUT", "END")]))
        spec = build_firmware_spec(proj, extras)
        chain = spec["led_chains"][0]
        self.assertFalse(chain["verified"])
        self.assertEqual(chain["mcu_net"], "FLOATING")


class GroupingTests(unittest.TestCase):
    def test_identical_unknowns_ask_once(self):
        pins = lambda s: [("1", "A", "N_%s_A" % s), ("2", "B", "N_%s_B" % s),
                          ("3", "C", "N_%s_C" % s), ("4", "D", "N_%s_D" % s)]
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "N1")]),
            part("U10", "MYSTERY", "QFN-16", pins("X")),
            part("U11", "MYSTERY", "QFN-16", pins("X")))
        spec = build_firmware_spec(proj, extras)
        asked = [q for q in needs(spec) if "U10" in q or "U11" in q]
        self.assertEqual(len(asked), 1)
        self.assertIn("U10", asked[0])
        self.assertIn("U11", asked[0])

    def test_different_unknowns_ask_separately(self):
        proj, extras = project(
            part("U2", "RP2040", "QFN-56", [("1", "GPIO1", "N1")]),
            part("U10", "MYSTERY_A", "QFN-16",
                 [("1", "A", "NA"), ("2", "B", "NB"), ("3", "C", "NC"), ("4", "D", "ND")]),
            part("U11", "MYSTERY_B", "QFN-16",
                 [("1", "A", "MA"), ("2", "B", "MB"), ("3", "C", "MC"), ("4", "D", "MD")]))
        spec = build_firmware_spec(proj, extras)
        self.assertEqual(len([q for q in needs(spec) if "U10" in q]), 1)
        self.assertEqual(len([q for q in needs(spec) if "U11" in q]), 1)

    def test_missing_mcu_still_asked(self):
        proj, extras = project(
            part("U10", "MYSTERY", "QFN-16",
                 [("1", "A", "NA"), ("2", "B", "NB"), ("3", "C", "NC"), ("4", "D", "ND")]))
        spec = build_firmware_spec(proj, extras)
        self.assertTrue(any("MCU" in q for q in needs(spec)))


if __name__ == "__main__":
    unittest.main()
