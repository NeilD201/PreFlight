import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.board_measurements import dc_trace, measure_board
from pcb_checker.parsers.sexpr import parse


def segment(a, b, width=.2, layer='F.Cu'):
    return {'start': a, 'end': b, 'width_mm': width, 'layer': layer}


class BoardMeasurementsTests(unittest.TestCase):
    def test_series_chain_with_width_change_uses_each_cross_section(self):
        result = dc_trace([segment([0,0],[10,0],.2), segment([10,0],[10,10],.4)], {'F.Cu': .035}, set())
        self.assertEqual(result['status'], 'estimated')
        self.assertAlmostEqual(result['ohms'], 17e-6*(10/.2+10/.4)/.035, places=7)
        self.assertEqual(result['temperature_c'], 25)
        self.assertEqual(result['length_mm'], 20)

    def test_does_not_turn_branches_loops_gaps_or_intersections_into_series_resistance(self):
        shapes = [
            [([0,0],[1,0]),([1,0],[2,0]),([1,0],[1,1])],
            [([0,0],[1,0]),([1,0],[1,1]),([1,1],[0,0])],
            [([0,0],[1,0]),([2,0],[3,0])],
            [([0,0],[2,2]),([2,2],[2,0]),([2,0],[0,2])],
            [([0,0],[2,0]),([2,0],[1,0])],
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertEqual(dc_trace([segment(a,b) for a,b in shape], {'F.Cu':.035}, set())['status'], 'unavailable')

    def test_no_default_copper_or_unsupported_geometry_estimate(self):
        drawn = [segment([0,0],[1,0])]
        for excluded in ({'vias'}, {'copper pours'}, {'curved traces'}):
            self.assertEqual(dc_trace(drawn, {'F.Cu':.035}, excluded)['status'], 'unavailable')
        self.assertEqual(dc_trace(drawn, {}, set())['status'], 'unavailable')
        self.assertEqual(dc_trace([segment([0,0],[1,0],0)], {'F.Cu':.035}, set())['status'], 'unavailable')

    def test_saved_stackup_and_zone_exclusion_are_read_from_board(self):
        board = parse('(kicad_pcb (net 1 "VDD") (setup (stackup (layer "F.Cu" (type "copper") (thickness .07)))) (zone (net 1)))', 'kicad_pcb')
        result = measure_board(board, {'VDD':[segment([0,0],[10,0])]}, {})
        self.assertEqual(result['stackup']['layers'][0]['thickness_mm'], .07)
        self.assertIn('copper pours', result['nets'][0]['dc_trace']['reason'])


if __name__ == '__main__':
    unittest.main()
