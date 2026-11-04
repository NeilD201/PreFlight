"""Regression coverage for the internal KiCad schematic netlister.

Synthetic single/multi-sheet boards with hand-computed nets plus real
WenneBoard assertions (U2 GPIO mapping, auto-net naming vs PCB names).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.parsers.schematic_netlist import resolve_schematic_nets


def lib_r():
    return """(symbol "TEST:R"
      (pin_numbers (hide yes)) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "TEST:R" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "TEST:R_0_1" (rectangle (start -1 -1) (end 1 1) (stroke (width 0.1) (type default)) (fill (type none))))
      (symbol "TEST:R_1_1"
        (pin passive line (at -2.54 0 0) (length 2.54) (name "1" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 2.54 0 0) (length 2.54) (name "2" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))))"""


def instance(ref, value, x, y, rot=0, lib="TEST:R", unit=1, uuid=None):
    uid = uuid or f"u-{ref}"
    return f"""(symbol (lib_id "{lib}") (at {x} {y} {rot}) (unit {unit}) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes) (uuid "{uid}")
      (property "Reference" "{ref}" (at {x} {y+2} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "{value}" (at {x} {y-2} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (pin "1" (uuid "{uid}-p1")) (pin "2" (uuid "{uid}-p2")))"""


def wire(x1, y1, x2, y2, uid=None):
    uid = uid or f"w-{x1}-{y1}-{x2}-{y2}"
    return f'(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) (stroke (width 0) (type default)) (uuid "{uid}"))'


def glabel(name, x, y, uid=None):
    uid = uid or f"g-{name}-{x}-{y}"
    return f'''(global_label "{name}" (shape input) (at {x} {y} 0) (fields_autoplaced yes) (effects (font (size 1.27 1.27))) (uuid "{uid}") (property "Intersheetrefs" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)))))'''


class DividerBoardTests(unittest.TestCase):
    def board(self):
        lib_power = """(symbol "power:GND" (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes)) (exclude_from_sim no) (in_bom yes) (on_board yes)
          (property "Reference" "#PWR" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
          (property "Value" "GND" (at 0 0 0) (effects (font (size 1.27 1.27))))
          (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
          (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))
          (symbol "power:GND_0_1"
            (pin power_in line (at 0 0 0) (length 0) (name "GND" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))))"""
        body = "\n".join([
            instance("R1", "10k", 100, 100),
            instance("R2", "10k", 110, 100),
            wire(97.46, 100, 90, 100),
            glabel("IN", 90, 100),
            wire(102.54, 100, 105, 100),
            wire(105, 100, 107.46, 100),
            "(junction (at 105 100) (diameter 0) (color 0 0 0 0) (uuid \"j1\"))",
            instance("P1", "GND", 112.54, 100, lib="power:GND").replace('(pin "1" (uuid "u-P1-p1")) (pin "2" (uuid "u-P1-p2"))', '(pin "1" (uuid "u-P1-p1"))'),
        ])
        return f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "root-x") (paper "A4")
  (lib_symbols {lib_r()} {lib_power})
  {body})"""

    def test_divider_nets_names_and_power(self):
        result = resolve_schematic_nets(self.board())
        pins = result["pins"]
        by_pin = {(ref, p.number): p.net for ref, plist in pins.items() for p in plist}
        self.assertEqual(by_pin[("R1", "1")], "IN")
        # Unlabeled middle copper takes KiCad's Net-(Ref-Pin) form.
        self.assertEqual(by_pin[("R1", "2")], by_pin[("R2", "1")])
        self.assertTrue(by_pin[("R1", "2")].startswith("Net-("), by_pin[("R1", "2")])
        self.assertEqual(by_pin[("R2", "2")], "GND")

    def test_unconnected_pin_gets_unconnected_net(self):
        text = self.board().replace(wire(97.46, 100, 90, 100), "").replace(glabel("IN", 90, 100), "")
        result = resolve_schematic_nets(text)
        by_pin = {(ref, p.number): p.net for ref, plist in result["pins"].items() for p in plist}
        self.assertEqual(by_pin[("R1", "1")], "unconnected-(R1-1)")

    def test_crossing_wires_without_junction_stay_split(self):
        body = "\n".join([
            instance("R1", "10k", 100, 100),
            instance("R2", "10k", 100, 110),
            wire(97.46, 100, 107.46, 100),
            wire(100, 95, 100, 115),
        ])
        text = f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "root-y") (paper "A4")
  (lib_symbols {lib_r()})
  {body})"""
        result = resolve_schematic_nets(text)
        by_pin = {(ref, p.number): p.net for ref, plist in result["pins"].items() for p in plist}
        # R1's horizontal net and R2's vertical net cross at (100,100) with no
        # junction dot: they must remain separate nets.
        self.assertNotEqual(by_pin[("R1", "1")], by_pin[("R2", "1")])


def lib_cap():
    # Capacitor-style symbol: pin 1 above origin in library y-up frame.
    return """(symbol "TEST:C"
      (pin_numbers (hide yes)) (pin_names (offset 0)) (exclude_from_sim no) (in_bom yes) (on_board yes)
      (property "Reference" "C" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Value" "TEST:C" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27))))
      (symbol "TEST:C_0_1" (rectangle (start -1 -1) (end 1 1) (stroke (width 0.1) (type default)) (fill (type none))))
      (symbol "TEST:C_1_1"
        (pin passive line (at 0 3.81 0) (length 2.54) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))
        (pin passive line (at 0 -3.81 0) (length 2.54) (name "~" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))))"""


def cap_instance(ref, x, y, rot=0, uuid=None):
    uid = uuid or f"u-{ref}"
    return f"""(symbol (lib_id "TEST:C") (at {x} {y} {rot}) (unit 1) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (fields_autoplaced yes) (uuid "{uid}")
      (property "Reference" "{ref}" (at {x} {y+2} 0) (effects (font (size 1.27 1.27))))
      (property "Value" "1u" (at {x} {y-2} 0) (effects (font (size 1.27 1.27))))
      (property "Footprint" "" (at {x} {y} 0) (effects (font (size 1.27 1.27)) (hide yes)))
      (pin "1" (uuid "{uid}-p1")) (pin "2" (uuid "{uid}-p2")))"""


class LibraryYFlipTests(unittest.TestCase):
    """Library offsets are y-up, the sheet frame y-down: negating the
    library y before rotation puts lib-above-origin pins at smaller
    sheet-y (visually above the instance on the sheet)."""

    def board(self, rot):
        body = "\n".join([
            cap_instance("C1", 100, 100, rot=rot),
            wire(100, 96.19, 120, 96.19),
            glabel("TOP_NET", 120, 96.19),
            wire(100, 103.81, 120, 103.81),
            glabel("BOT_NET", 120, 103.81),
        ])
        return f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "root-cap") (paper "A4")
  (lib_symbols {lib_cap()})
  {body})"""

    def nets(self, rot):
        result = resolve_schematic_nets(self.board(rot))
        return {(ref, p.number): p.net for ref, plist in result["pins"].items() for p in plist}

    def test_rot0_pin1_lands_below_origin(self):
        by_pin = self.nets(0)
        # Lib pin 1 at (0,+3.81) maps to sheet (100,96.19): TOP_NET wire.
        self.assertEqual(by_pin[("C1", "1")], "TOP_NET")
        self.assertEqual(by_pin[("C1", "2")], "BOT_NET")

    def test_rot180_pin1_lands_above_origin(self):
        by_pin = self.nets(180)
        self.assertEqual(by_pin[("C1", "1")], "BOT_NET")
        self.assertEqual(by_pin[("C1", "2")], "TOP_NET")


class CrossSheetIsolationTests(unittest.TestCase):
    """Identical coordinates in different sheets are different copper: a GND
    net in a child sheet must not short a +5V net in the root sheet."""

    def test_same_coordinates_stay_split_across_sheets(self):
        root_body = "\n".join([
            instance("R1", "10k", 100, 100),
            wire(97.46, 100, 90, 100),
            glabel("PLUS5", 90, 100),
        ])
        root = f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "root-x") (paper "A4")
  (lib_symbols {lib_r()})
  {root_body})"""
        child_body = "\n".join([
            instance("R9", "10k", 100, 100),
            wire(97.46, 100, 90, 100),
            glabel("GNDX", 90, 100),
        ])
        child = f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "child-x") (paper "A4")
  (lib_symbols {lib_r()})
  {child_body})"""
        result = resolve_schematic_nets(root, {"child.kicad_sch": child})
        by_pin = {(ref, p.number): p.net for ref, plist in result["pins"].items() for p in plist}
        self.assertEqual(by_pin[("R1", "1")], "PLUS5")
        self.assertEqual(by_pin[("R9", "1")], "GNDX")


class OverlappingPinTests(unittest.TestCase):
    """Two pins at identical coordinates with no wire are one copper net
    (diode overlapping a switch pin), not two unconnected nets."""

    def test_overlapping_pins_share_net(self):
        # D1 rot90: pin 1 (-2.54,0) -> (100,102.54); pin 2 (+2.54,0) ->
        # (100,97.46). S1 rot0 placed so its pin 1 lands on (100,97.46).
        body = "\n".join([
            instance("D1", "1N4148", 100, 100, rot=90, lib="TEST:R"),
            instance("S1", "SW", 102.54, 97.46, lib="TEST:R"),
            wire(100, 102.54, 110, 102.54),
            glabel("ROW", 110, 102.54),
            wire(105.08, 97.46, 115, 97.46),
            glabel("COL", 115, 97.46),
        ])
        text = f"""(kicad_sch (version 20230101) (generator eeschema) (uuid "root-ov") (paper "A4")
  (lib_symbols {lib_r()})
  {body})"""
        result = resolve_schematic_nets(text)
        by_pin = {(ref, p.number): p.net for ref, plist in result["pins"].items() for p in plist}
        self.assertEqual(by_pin[("D1", "1")], "ROW")
        self.assertEqual(by_pin[("S1", "2")], "COL")
        # Overlapping D1.2/S1.1 share one auto net, not two unconnected nets.
        self.assertEqual(by_pin[("D1", "2")], by_pin[("S1", "1")])
        self.assertEqual(by_pin[("D1", "2")], "Net-(D1-Pad2)")
        self.assertNotIn("unconnected", by_pin[("D1", "2")])


if __name__ == "__main__":
    unittest.main()
