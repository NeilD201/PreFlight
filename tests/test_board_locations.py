"""Marker geometry must come from the checked board and remain correctly transformed."""
import hashlib
import math
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.board_locations import board_locations, enrich_visual, merge_verified_source_artifacts


SOURCE=b'''(kicad_pcb (layers (0 "F.Cu" signal) (1 "In1.Cu" signal) (31 "B.Cu" signal))
 (net 1 "DATA")
 (footprint "Test" (layer "B.Cu") (at 10 20 90) (uuid "fp") (property "Reference" "U1")
   (pad "1" smd rect (at 2 3 90) (size 2 1) (layers "B.Cu" "B.Mask") (net 1 "DATA") (uuid "pad")))
 (segment (start 3 4) (end 5 6) (width .2) (layer "In1.Cu") (net 1) (uuid "track"))
 (arc (start 1 0) (mid 0 1) (end -1 0) (width .2) (layer "F.Cu") (net 1) (uuid "arc"))
 (via (at 3 4) (size .6) (drill .3) (layers "F.Cu" "B.Cu") (net 1) (uuid "via")))'''


class BoardLocations(unittest.TestCase):
    def test_only_hash_verified_off_outline_copper_is_merged_from_original_source(self):
        source=b'''(kicad_pcb
          (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
          (gr_line (start 0 0) (end 10 0) (stroke (width .05) (type default)) (layer "Edge.Cuts") (uuid "e1"))
          (gr_line (start 10 0) (end 10 -10) (stroke (width .05) (type default)) (layer "Edge.Cuts") (uuid "e2"))
          (gr_line (start 10 -10) (end 0 -10) (stroke (width .05) (type default)) (layer "Edge.Cuts") (uuid "e3"))
          (gr_line (start 0 -10) (end 0 0) (stroke (width .05) (type default)) (layer "Edge.Cuts") (uuid "e4"))
          (gr_line (start 20 -5) (end 22 -4) (stroke (width .2) (type default)) (layer "F.Cu") (uuid "stray")))'''
        digest=hashlib.sha256(source).hexdigest()
        base={'item_locations':{}}
        merged=merge_verified_source_artifacts(base,source,digest)
        self.assertTrue(merged['item_locations']['stray']['source_only'])
        self.assertEqual(merge_verified_source_artifacts(base,source,'wrong-hash'),base)

    def test_rotated_back_pad_center_and_envelope(self):
        g=board_locations(SOURCE);pad=g['item_locations']['pad']
        self.assertEqual(pad['point'],[13.,18.]);self.assertEqual(pad['layers'],['B.Cu'])
        self.assertEqual(pad['net'],'DATA');self.assertEqual(g['pad_locations'],{'U1':{'1':['pad']}})
        xs=[p[0] for p in pad['points']];ys=[p[1] for p in pad['points']]
        self.assertAlmostEqual(max(xs)-min(xs),1);self.assertAlmostEqual(max(ys)-min(ys),2)

    def test_via_spans_inner_layers_and_keeps_drill_dimensions(self):
        via=board_locations(SOURCE)['item_locations']['via']
        self.assertEqual(via['layers'],['F.Cu','In1.Cu','B.Cu'])
        self.assertEqual(via['radius_mm'],.3);self.assertEqual(via['drill_mm'],.3)

    def test_arc_follows_curve_not_chord(self):
        arc=board_locations(SOURCE)['item_locations']['arc']
        self.assertTrue(any(math.dist(p,[0,1])<.04 for p in arc['points']))
        self.assertTrue(all(abs(math.hypot(*p)-1)<1e-9 for p in arc['points']))

    def test_geometry_upgrade_requires_matching_checked_snapshot(self):
        digest=hashlib.sha256(SOURCE).hexdigest();meta={'status':'ready','analyzed_pcb_sha256':digest}
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'analyzed-board.kicad_pcb';p.write_bytes(SOURCE)
            self.assertEqual(enrich_visual(meta,p,digest)['item_locations']['pad']['point'],[13,18])
            p.write_bytes(SOURCE+b'\n')
            self.assertNotIn('item_locations',enrich_visual(meta,p,digest))
            self.assertNotIn('item_locations',enrich_visual(meta,p,'other'))


if __name__=='__main__':unittest.main()
