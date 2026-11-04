"""Pad rotation convention regression tests (independent Debugger collateral).

KiCad footprint `at` angles rotate pad offsets clockwise in board
coordinates (y-axis down). A CCW-math rotation displaces every pad of a
rotated footprint by twice its local offset and manufactures false
"copper open" findings (e.g. R5-1 on WenneBoard D+ reported 1.02 mm from
copper that it actually touches).
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _pcb_bootstrap  # noqa
from pcb_checker.parsers.sexpr import parse
from pcb_checker import routing_analysis as ra


def _board():
    return """(kicad_pcb (version 20221001) (generator "t") (uuid "b")
  (general (thickness 1.6)) (paper "A4")
  (layers
  (0 "F.Cu" signal)
  (1 "B.Cu" signal)
  )
  (net 0 "")
  (net 1 "D+")
  (segment (start 125.44 41.79) (end 124.9 41.25) (width 0.2) (layer "F.Cu") (net 1))
  (footprint "Resistor_SMD:R_0402_1005Metric" (layer "F.Cu") (tstamp "R5") (at 125.44 42.3025 -90)
    (fp_text reference "R5" (at 0 0))
    (pad "1" smd (at -0.51 0) (size 0.54 0.64) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "D+"))
    (pad "2" smd (at 0.51 0) (size 0.54 0.64) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "D+"))
  )
)"""


def test_rotated_pad_lands_on_copper():
    copper = ra.extract_copper(parse(_board(), 'kicad_pcb'))
    pads = {p['pin']: p for p in copper['nets']['D+']['pads']}
    assert abs(pads['1']['x'] - 125.44) < 1e-9, pads['1']
    assert abs(pads['1']['y'] - 41.7925) < 1e-6, pads['1']
    graph = ra.build_graph(copper['nets']['D+'], copper['layers'])
    topology = ra.pin_topology(graph, copper['nets']['D+']['pads'])
    assert topology['disconnected'] == [], topology['disconnected']


def test_pad_rotation_matches_kicad_convention():
    # -90 deg footprint rotation maps local (-0.51, 0) to board (0, -0.51):
    # clockwise in y-down board coordinates.
    theta = math.radians(-90)
    dx, dy = -0.51, 0.0
    assert abs(dx * math.cos(theta) + dy * math.sin(theta) - 0.0) < 1e-9
    assert abs(-dx * math.sin(theta) + dy * math.cos(theta) - -0.51) < 1e-9
