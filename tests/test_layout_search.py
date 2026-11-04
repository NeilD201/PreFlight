import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.layout_search import placement_candidates, edge_segments, box_inside
from pcb_checker.parsers.sexpr import parse


def board(points, extra=''):
    edges=''.join(f'(gr_line (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (layer "Edge.Cuts"))'
                  for a,b in zip(points,points[1:]+points[:1]))
    return parse('(kicad_pcb '+edges+extra+')','kicad_pcb')


def footprint(ref='NEW', x=0, y=0, locked=False):
    return f'''(footprint "Test:Part" {'locked' if locked else ''} (layer "F.Cu")
       (at {x} {y}) (property "Reference" "{ref}")
       (fp_rect (start -1 -1) (end 1 1) (layer "F.CrtYd"))
       (pad "1" smd rect (at -0.5 0) (size 0.5 0.5) (layers "F.Cu")))'''


class PlacementSearchTests(unittest.TestCase):
    def test_finds_alternatives_outside_obstacle_and_preserves_board(self):
        root=board([(0,0),(12,0),(12,12),(0,12)],footprint('U1',6,6))
        original=repr(root)
        result=placement_candidates(root,parse(footprint(),'footprint'),anchors=[(6,6)])
        self.assertGreater(result['positions_checked'],100)
        self.assertEqual(len(result['candidates']),6)
        self.assertTrue(all(not c['overlapping_references'] for c in result['candidates']))
        self.assertEqual(repr(root),original)

    def test_concave_outline_and_cutout_are_not_treated_as_free_space(self):
        root=board([(0,0),(12,0),(12,4),(4,4),(4,12),(0,12)])
        self.assertFalse(box_inside([6,6,8,8],edge_segments(root)))
        result=placement_candidates(root,parse(footprint(),'footprint'))
        self.assertTrue(result['candidates'])
        self.assertTrue(all(c['envelope_mm'][2]<4 or c['envelope_mm'][3]<4 for c in result['candidates']))
        hole=board([(0,0),(12,0),(12,12),(0,12)],
                   '(gr_rect (start 5 5) (end 6 6) (layer "Edge.Cuts"))')
        self.assertFalse(box_inside([4,4,7,7],edge_segments(hole)))

    def test_locked_parts_are_excluded_and_movable_collisions_named(self):
        outline=[(0,0),(4,0),(4,4),(0,4)]
        moving=placement_candidates(board(outline,footprint('U1',2,2)),parse(footprint(),'footprint'))
        self.assertTrue(moving['candidates'])
        self.assertTrue(all('U1' in c['overlapping_references'] for c in moving['candidates']))
        locked=placement_candidates(board(outline,footprint('U1',2,2,True)),parse(footprint(),'footprint'))
        self.assertEqual(locked['candidates'],[])

    def test_open_outline_is_explicitly_unavailable(self):
        root=parse('(kicad_pcb (gr_line (start 0 0) (end 12 0) (layer "Edge.Cuts")))','kicad_pcb')
        result=placement_candidates(root,parse(footprint(),'footprint'))
        self.assertEqual(result['candidates'],[])
        self.assertIn('open',result['reason'])
