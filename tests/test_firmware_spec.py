import unittest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.firmware.build_spec import build_firmware_spec, _pair_i2c_buses


def pin(number, name, net, electrical_type=""):
    return {"number": number, "name": name, "net": net,
            "electrical_type": electrical_type, "no_connect": False}


def ic(ref="U2", value="MAX30001", nets=("SDA", "SCL", "VDD", "GND", "INT", "X", "Y", "Z")):
    return {
        "reference": ref,
        "schematic": [{"reference": ref, "value": value, "footprint": "PKG-8",
                       "source_file": "demo.kicad_sch", "line": 4, "uuid": "u2",
                       "properties": {}}],
        "pcb": [{"reference": ref, "value": value, "footprint": "PKG-8",
                 "source_file": "demo.kicad_pcb", "line": 5, "uuid": "u2",
                 "pads": [{"number": str(n + 1), "net": net, "line": 1}
                          for n, net in enumerate(nets)]
                    }],
        "schematic_pins": [], "bom": [], "cpl": [],
    }


def connector(ref, sda, scl, value="Conn_01x04_Socket"):
    nets = ["+3.3V", sda, scl, "GND"]
    return {
        "reference": ref,
        "schematic": [{"reference": ref, "value": value, "footprint": "PinHeader_1x04",
                       "source_file": "demo.kicad_sch", "line": 4, "uuid": ref,
                       "properties": {}}],
        "pcb": [], "schematic_pins": [pin(str(n + 1), "", net) for n, net in enumerate(nets)],
        "bom": [], "cpl": [],
    }


def mcu_component(ref="U1", value="nRF52840_QIAA", mpn="nRF52840-QIAA"):
    return {
        "reference": ref,
        "schematic": [{"reference": ref, "value": value, "footprint": "QFN-73",
                       "source_file": "demo.kicad_sch", "line": 10, "uuid": "u1",
                       "properties": {"MPN": mpn}}],
        "pcb": [{"reference": ref, "value": value, "footprint": "QFN-73",
                 "source_file": "demo.kicad_pcb", "line": 20, "uuid": "u1",
                 "pads": [{"number": "29", "net": "SDA0", "line": 1},
                          {"number": "31", "net": "SCL0", "line": 2}]}],
        "schematic_pins": [pin("29", "P0.29", "SDA0"),
                           pin("31", "P0.31", "SCL0"),
                           pin("1", "VDD", "VDD", "power_in")],
        "bom": [], "cpl": [],
    }


def project_dict(*components, circuit_status="available", purpose=""):
    comp_map = {c["reference"]: c for c in components}
    identities = {}
    for c in components:
        props = {}
        for record in (c.get("schematic") or []):
            props.update(record.get("properties") or {})
        identities[c["reference"]] = {
            "value": (c.get("schematic") or [{}])[0].get("value", ""),
            "footprint": (c.get("schematic") or [{}])[0].get("footprint", ""),
            "mpn": props.get("MPN", ""), "manufacturer": props.get("MFR", ""),
            "part_rule": "", "lcsc": "", "datasheet": "",
        }
    return {
        "components": comp_map,
        "circuit": {"status": circuit_status, "identities": identities,
                    "supply_nets": ["VDD"] if circuit_status == "available" else [],
                    "bus_candidates": [{
                        "net": "SDA0", "members": ["U1", "U2"],
                        "pins": [{"reference": "U1", "pin": "29", "name": "P0.29"},
                                 {"reference": "U2", "pin": "5", "name": "SDA"}],
                        "pullups": []}] if circuit_status == "available" else []},
        "schematic_nets": {"SDA0": ["U1-29"], "GND": ["U1-2"]} if circuit_status == "available" else {},
        "pcb_nets": {},
        "design_context": {"purpose": purpose} if purpose else {},
    }


class BuildSpecTests(unittest.TestCase):
    def test_identifies_mcu_with_gpio_map_and_buses(self):
        sensor = {"reference": "U2", "schematic": [], "pcb": [],
                  "schematic_pins": [pin("5", "SDA", "SDA0")], "bom": [], "cpl": []}
        project = project_dict(mcu_component(), sensor, purpose="Log temperature")
        project["circuit"]["identities"]["U2"] = {
            "value": "SHT41", "footprint": "", "mpn": "SHT41-AD1B",
            "manufacturer": "Sensirion", "part_rule": "", "lcsc": "", "datasheet": ""}
        spec = build_firmware_spec(project)
        self.assertEqual(spec["mcu"]["status"], "identified")
        self.assertEqual(spec["mcu"]["reference"], "U1")
        self.assertEqual(spec["mcu"]["family"], "nRF52")
        self.assertEqual(spec["mcu"]["confidence"], "high")
        self.assertTrue(spec["mcu"]["evidence"])
        rows = {r["pin"]: r for r in spec["gpio_map"]}
        self.assertEqual(rows["29"]["schematic_net"], "SDA0")
        self.assertEqual(rows["29"]["pcb_net"], "SDA0")
        self.assertTrue(rows["29"]["match"])
        self.assertEqual(spec["power"]["supply_nets"], ["VDD"])
        self.assertEqual(spec["power"]["ground_nets"], ["GND"])
        self.assertEqual(len(spec["buses"]), 1)
        self.assertEqual(spec["buses"][0]["mcu_pins"][0]["pin"], "29")
        self.assertEqual([p["reference"] for p in spec["peripherals"]], ["U2"])
        self.assertEqual(spec["stated_purpose"], "Log temperature")
        self.assertEqual(spec["open_questions"], [])

    def test_unidentified_mcu_becomes_open_question(self):
        plain = {"reference": "U1", "schematic": [], "pcb": [],
                 "schematic_pins": [pin("1", "VDD", "VDD")], "bom": [], "cpl": []}
        spec = build_firmware_spec(project_dict(plain))
        self.assertEqual(spec["mcu"]["status"], "unidentified")
        self.assertEqual(spec["gpio_map"], [])
        by_resolve = {}
        for question in spec["open_questions"]:
            by_resolve.setdefault(question["resolves"], []).append(question)
        self.assertEqual(len(by_resolve["mcu"]), 1)
        # No stated purpose either, so behavior is asked about as well.
        self.assertEqual(len(by_resolve["purpose"]), 1)

    def test_ambiguous_mcu_lists_candidates(self):
        spec = build_firmware_spec(project_dict(
            mcu_component("U1"), mcu_component("U2", value="ESP32-S3", mpn="ESP32-S3")))
        self.assertEqual(spec["mcu"]["status"], "ambiguous")
        self.assertEqual(len(spec["mcu"]["candidates"]), 2)
        self.assertTrue(any(q["resolves"] == "mcu" for q in spec["open_questions"]))

    def test_pad_nets_back_gpio_without_schematic_pins(self):
        component = mcu_component()
        component["schematic_pins"] = []
        spec = build_firmware_spec(project_dict(component))
        rows = {r["pin"]: r for r in spec["gpio_map"]}
        # PCB-only rows are shown as unverified, never as agreement.
        self.assertEqual(rows["29"]["pcb_net"], "SDA0")
        self.assertEqual(rows["29"]["schematic_net"], "")
        self.assertFalse(rows["29"]["match"])

    def test_pad_names_carry_into_gpio_rows(self):
        component = mcu_component()
        component["schematic_pins"] = []
        for record in component["pcb"]:
            for pad in record["pads"]:
                pad["name"] = {"29": "AIN5/P0.29", "31": "AIN4/P0.31"}.get(pad["number"], "")
        spec = build_firmware_spec(project_dict(component))
        rows = {r["pin"]: r for r in spec["gpio_map"]}
        # Footprint pin names are parsed CAD facts; P-names map to ports.
        self.assertEqual(rows["29"]["name"], "AIN5/P0.29")
        self.assertEqual(rows["31"]["name"], "AIN4/P0.31")

    def test_connected_unidentified_ic_gets_open_question(self):
        spec = build_firmware_spec(project_dict(mcu_component(), ic()))
        questions = [q for q in spec["open_questions"] if q["resolves"] == "peripheral"]
        self.assertEqual(len(questions), 1)
        self.assertIn("U2", questions[0]["need"])
        self.assertIn("MAX30001", questions[0]["need"])
        unknowns = [p for p in spec["peripherals"] if p["reference"] == "U2"]
        self.assertEqual(len(unknowns), 1)
        self.assertFalse(unknowns[0]["identified"])

    def test_identical_connectors_on_different_buses_ask_once(self):
        parts = [connector("P%d" % i, "SD%d" % i, "SC%d" % i) for i in range(3, 10)]
        spec = build_firmware_spec(project_dict(mcu_component(), *parts))
        questions = [q for q in spec["open_questions"] if q["resolves"] == "peripheral"]
        self.assertEqual(len(questions), 1)
        need = questions[0]["need"]
        for ref in ("P3", "P4", "P5", "P6", "P7", "P8", "P9"):
            self.assertIn(ref, need)
        # Per-member bus distinctions are preserved, not merged away.
        self.assertIn("SD3", need)
        self.assertIn("SD9", need)
        scope_nets = questions[0]["scope"]["nets"]
        self.assertIn("SD3", scope_nets)
        self.assertIn("SC9", scope_nets)

    def test_j_expansion_connectors_classify_as_i2c_ports(self):
        parts = [connector("J%d" % i, "SD%d" % i, "SC%d" % i) for i in (3, 4, 5)]
        spec = build_firmware_spec(project_dict(mcu_component(), *parts))
        self.assertFalse([q for q in spec["open_questions"] if q["resolves"] == "peripheral"])
        ports = [p for p in spec["peripherals"] if p["reference"] in ("J3", "J4", "J5")]
        self.assertEqual(len(ports), 3)
        self.assertTrue(all(p["part_rule"] == "i2c_expansion_port" for p in ports))

    def test_two_pin_passive_needs_no_question(self):
        resistor = {"reference": "R1",
                    "schematic": [{"reference": "R1", "value": "10k", "footprint": "R_0402",
                                   "source_file": "demo.kicad_sch", "line": 6, "uuid": "r",
                                   "properties": {}}],
                    "pcb": [], "schematic_pins": [pin("1", "", "SDA"), pin("2", "", "VDD")],
                    "bom": [], "cpl": []}
        spec = build_firmware_spec(project_dict(mcu_component(), resistor))
        self.assertFalse([q for q in spec["open_questions"] if q["resolves"] == "peripheral"])
        # Settled passives are listed as identified support parts (no driver
        # needed) rather than vanishing: the question stays absent.
        identified = [p for p in spec["peripherals"] if p["reference"] == "R1"]
        self.assertEqual(len(identified), 1)
        self.assertTrue(identified[0]["identified"])
        self.assertEqual(identified[0]["part_rule"], "passive_two_terminal")

    def test_single_net_i2c_entries_pair_into_one_bus(self):
        buses = _pair_i2c_buses([
            {"kind": "I2C", "nets": ["SDA"], "members": ["U1", "U4"],
             "mcu_pins": [], "all_pins": [], "pullups": [{"reference": "R16"}]},
            {"kind": "I2C", "nets": ["SCL"], "members": ["U1"],
             "mcu_pins": [], "all_pins": [], "pullups": [{"reference": "R4"}]},
        ])
        self.assertEqual(len(buses), 1)
        self.assertEqual(buses[0]["nets"], ["SDA", "SCL"])
        self.assertEqual(buses[0]["members"], ["U1", "U4"])
        self.assertEqual([p["reference"] for p in buses[0]["pullups"]], ["R16", "R4"])

    def test_numbered_bus_pairs_match_by_suffix_only(self):
        buses = _pair_i2c_buses([
            {"kind": "I2C", "nets": ["SDA0"], "members": [], "mcu_pins": [],
             "all_pins": [], "pullups": []},
            {"kind": "I2C", "nets": ["SCL1"], "members": [], "mcu_pins": [],
             "all_pins": [], "pullups": []},
        ])
        # Same-role pairing across different numbered buses would invent a
        # bus that does not exist: both entries stay single.
        self.assertEqual(len(buses), 2)
        self.assertTrue(all(len(b["nets"]) == 1 for b in buses))

    def test_unavailable_connectivity_stays_unresolved(self):
        spec = build_firmware_spec(project_dict(mcu_component(), circuit_status="unavailable"))
        # Declared values still identify the MCU and the CAD-declared pins
        # are shown, but compared connectivity (buses, verified agreement)
        # stays empty until the review provides it.
        self.assertEqual(spec["mcu"]["status"], "identified")
        self.assertTrue(spec["gpio_map"])
        self.assertEqual(spec["buses"], [])
        self.assertTrue(any(q["resolves"] == "connectivity" for q in spec["open_questions"]))


if __name__ == "__main__":
    unittest.main()
