import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.schematic_edits import add_reference_antenna
from pcb_checker.parsers.sexpr import parse
from pcb_checker.parsers.kicad_schematic import parse_schematic


class SchematicAntennaTests(unittest.TestCase):
    def test_adds_a_real_two_pad_symbol_feed_label_and_explicit_nc(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'demo.kicad_sch'
            c33='''(symbol (lib_id "Device:C") (at 50 50 0) (unit 1) (uuid "c33-uuid")
              (property "Reference" "C33") (property "Value" "0.5pF")
              (property "Footprint" "Cap:C_0402") (pin "1" (uuid "pin1"))
              (instances (project "demo" (path "/root" (reference "C33") (unit 1)))))'''
            device_c='''(symbol "Device:C" (pin_names (offset 1.016)) (pin_numbers (hide yes))
              (property "Reference" "C") (property "Value" "C")
              (symbol "C_0_1") (symbol "C_1_1"
                (pin passive line (at 0 -2.54 90) (length 2.54) (name "~") (number "1"))
                (pin passive line (at 0 2.54 90) (length 2.54) (name "~") (number "2"))))'''
            path.write_text('(kicad_sch (version 20250114) (uuid "root") (lib_symbols '+device_c+') '+c33+')')
            result=add_reference_antenna(path,reference='AE1',value='2450AT18A0100001E',
                footprint='RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz',
                datasheet='https://www.johansontechnology.com/docs/3827/Antenna-2450AT18A0100001E-Rev4.0.pdf',
                net='Net-(C33-Pad1)',anchor_reference='C33',anchor_pin='1')
            text=path.read_text()
            parse(text,'kicad_sch')
            self.assertEqual(result['reference'],'AE1')
            self.assertIn('(number "2"',text)
            self.assertIn('(no_connect (at ',text)
            self.assertIn('(wire (pts (xy 50.0 52.54) (xy ',text)
            self.assertNotIn('(global_label "Net-(C33-Pad1)"',text)
            record=next(c for c in parse_schematic(text,'demo.kicad_sch').components if c.reference=='AE1')
            self.assertEqual(record.value,'2450AT18A0100001E')
            self.assertEqual(record.footprint,'RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz')
            with self.assertRaisesRegex(ValueError,'already exists'):
                add_reference_antenna(path,reference='AE1',value='x',footprint='RF_Antenna:x',datasheet='~',net='N',
                                       anchor_reference='C33',anchor_pin='1')


if __name__=='__main__':unittest.main()
