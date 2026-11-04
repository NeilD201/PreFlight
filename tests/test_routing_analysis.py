"""Regression coverage for the general-purpose routing analyzer.

Synthetic unfamiliar PCBs (USB and non-USB pairs, arcs, vias, branches,
mixed layers, acute/obtuse bends) with hand-computed expectations.
"""

import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.models import Component, ComponentRecord, Project
from pcb_checker.parsers.sexpr import parse
from pcb_checker import routing_analysis as ra


def board(nets, body):
    net_lines = "\n".join(f'  (net {i} "{name}")' for i, name in enumerate([""] + nets))
    return f"""(kicad_pcb (version 20221001) (generator "test") (uuid "board-uuid")
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
  (setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035)) (layer "B.Cu" (type "copper") (thickness 0.035))))
{net_lines}
{body}
)"""


def seg(net, x1, y1, x2, y2, layer="F.Cu", width=0.25):
    return (f'  (segment (start {x1} {y1}) (end {x2} {y2}) (width {width}) '
            f'(layer "{layer}") (net {net}))')


def via(net, x, y):
    return f'  (via (at {x} {y}) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net {net}))'


def arc(net, x1, y1, xm, ym, x2, y2, layer="F.Cu", width=0.25):
    return (f'  (arc (start {x1} {y1}) (mid {xm} {ym}) (end {x2} {y2}) (width {width}) '
            f'(layer "{layer}") (net {net}))')


def footprint(ref, x, y, pads):
    lines = [f'  (footprint "Test:{ref}" (layer "F.Cu") (tstamp "{ref}") (at {x} {y})',
             f'    (fp_text reference "{ref}" (at 0 0))',
             f'    (fp_text value "v" (at 0 1))']
    for number, dx, dy, net_id, net_name in pads:
        lines.append(f'    (pad "{number}" smd (at {dx} {dy}) (size 1 1) '
                     f'(layers "F.Cu" "F.Paste" "F.Mask") (net {net_id} "{net_name}"))')
    lines.append("  )")
    return "\n".join(lines)


USB_BODY = "\n".join([
    seg(1, 0, 0, 10, 0),
    seg(2, 0, 1, 12, 1),
    footprint("J1", 0, 0, [("5", 0, 0, 1, "USB_DP"), ("6", 0, 1, 2, "USB_DM")]),
    footprint("J2", 10, 0, [("5", 0, 0, 1, "USB_DP"), ("6", 2, 1, 2, "USB_DM")]),
])

USB_BOARD = board(["USB_DP", "USB_DM"], USB_BODY)


class DiffPairDetectionTests(unittest.TestCase):
    def test_usb_pair_detected(self):
        self.assertEqual(ra.find_diff_pairs(["USB_DP", "USB_DM", "GND"]), [["USB_DP", "USB_DM"]])

    def test_generic_conventions(self):
        names = ["TX_P", "TX_N", "RX_P", "RX_N", "D+", "D-", "CLK_DP", "CLK_DM"]
        self.assertEqual(ra.find_diff_pairs(names),
                         [["CLK_DP", "CLK_DM"], ["D+", "D-"], ["RX_P", "RX_N"], ["TX_P", "TX_N"]])

    def test_single_ended_and_power_never_pair(self):
        names = ["SDA", "SCL", "GND", "AGND", "VCC", "+5V", "SPAN", "DPLUS",
                 "Net-(U1-Pad3)", "unconnected-(U2-Pad1)", "Q_CLK", "QSPI_SS"]
        self.assertEqual(ra.find_diff_pairs(names), [])


class UsbMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.copper = ra.extract_copper(parse(USB_BOARD, "kicad_pcb"))

    def test_spans_and_mismatch(self):
        measurement, judgment, _guidance = ra.analyze_pair("USB_DP", "USB_DM", self.copper)
        self.assertAlmostEqual(measurement["span_p_mm"], 10.0, places=3)
        self.assertAlmostEqual(measurement["span_n_mm"], 12.0, places=3)
        self.assertAlmostEqual(measurement["mismatch_mm"], 2.0, places=3)
        self.assertIn("exceeds", judgment)

    def test_matched_pair_within_bound(self):
        text = board(["TX_P", "TX_N"],
                     seg(1, 0, 0, 8, 0) + "\n" + seg(2, 0, 1, 8, 1) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "TX_P"), ("2", 0, 1, 2, "TX_N")]) + "\n"
                     + footprint("J2", 8, 0, [("1", 0, 0, 1, "TX_P"), ("2", 0, 1, 2, "TX_N")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        measurement, judgment, _guidance = ra.analyze_pair("TX_P", "TX_N", copper)
        self.assertAlmostEqual(measurement["mismatch_mm"], 0.0, places=3)
        self.assertIn("within", judgment)

    def test_usb_fullspeed_gets_numeric_screening_verdict(self):
        from pcb_checker.models import Component, ComponentRecord, Project
        project = Project('usb')
        project.components['U2'] = Component('U2',
            [ComponentRecord('U2', 'RP2040', 'x', 't.kicad_sch', 1)], [])
        project.schematic_nets = {'USB_DP': ['U2.47'], 'USB_DM': ['U2.48']}
        measurement, judgment, guidance = ra.analyze_pair("USB_DP", "USB_DM", self.copper, project)
        self.assertIn('USB 2.0 full-speed via U2', judgment)
        self.assertIn('13.4 ps', judgment)
        self.assertIn('no length tuning', judgment)
        self.assertIn('No length tuning is indicated', guidance)
        self.assertAlmostEqual(measurement['skew_ps'], 13.4, places=1)

    def test_unknown_interface_keeps_generic_budget_message(self):
        judgment, _guidance = ra.pair_skew_assessment("TX_P", "TX_N", 2.0)
        self.assertIn('interface skew budget', judgment)
        self.assertNotIn('full-speed', judgment)

    def test_branch_does_not_inflate_span(self):
        text = board(["SIG"],
                     seg(1, 0, 0, 10, 0) + "\n" + seg(1, 5, 0, 5, 5) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J3", 5, 5, [("1", 0, 0, 1, "SIG")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["SIG"], copper["layers"])
        paths = ra.pin_topology(graph, copper["nets"]["SIG"]["pads"])
        self.assertAlmostEqual(paths["span_mm"], 10.0, places=3)


class ArcViaLayerTests(unittest.TestCase):
    def test_arc_length_is_analytic(self):
        self.assertAlmostEqual(ra._arc_length([0, 0], [1, 1], [2, 0]), math.pi, places=3)

    def test_arc_span_uses_curve_not_chord(self):
        text = board(["CURVE"],
                     arc(1, 0, 0, 1, 1, 2, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "CURVE")]) + "\n"
                     + footprint("J2", 2, 0, [("1", 0, 0, 1, "CURVE")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["CURVE"], copper["layers"])
        paths = ra.pin_topology(graph, copper["nets"]["CURVE"]["pads"])
        # Curve length, not the chord: pad copper may shorten the electrical
        # path slightly, but the span must stay within (chord sum, arc length].
        chord = math.dist([0, 0], [1, 1]) + math.dist([1, 1], [2, 0])
        self.assertGreater(paths["span_mm"], chord)
        self.assertLessEqual(paths["span_mm"], math.pi + 1e-6)

    def test_via_joins_layers(self):
        text = board(["LINK"],
                     seg(1, 0, 5, 5, 5) + "\n" + via(1, 5, 5) + "\n"
                     + seg(1, 5, 5, 9, 5, layer="B.Cu") + "\n" + via(1, 9, 5) + "\n"
                     + seg(1, 9, 5, 10, 5) + "\n"
                     + footprint("J1", 0, 5, [("1", 0, 0, 1, "LINK")]) + "\n"
                     + footprint("J2", 10, 5, [("1", 0, 0, 1, "LINK")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["LINK"], copper["layers"])
        paths = ra.pin_topology(graph, copper["nets"]["LINK"]["pads"])
        self.assertAlmostEqual(paths["span_mm"], 10.0, places=3)


class TopologyTests(unittest.TestCase):
    def series_board(self, ref="R10", value="27", dnp=False):
        text = board(["SIG"],
                     seg(1, 0, 0, 4, 0) + "\n" + seg(1, 6, 0, 10, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint(ref, 4, 0, [("2", 0, 0, 1, "SIG"), ("1", 2, 0, 1, "SIG")]) + "\n"
                     + footprint("U2", 10, 0, [("1", 0, 0, 1, "SIG")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["SIG"], copper["layers"])
        project = Project("topo")
        for name, val, is_dnp in ((ref, value, dnp),):
            rec = ComponentRecord(name, val, "pkg", "s.kicad_sch", 1, dnp=is_dnp)
            project.components[name] = Component(name, [rec], [], [])
        return ra.pin_topology(graph, copper["nets"]["SIG"]["pads"], project)

    def test_route_through_fitted_series_part(self):
        topo = self.series_board()
        routed = [r for r in topo["pairs"]
                  if "through" in r and r["pins"] == [["J1", "1"], ["U2", "1"]]]
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0]["through"], [{"reference": "R10", "value": "27"}])
        self.assertAlmostEqual(routed[0]["length_mm"], 8.0, places=3)
        self.assertEqual(topo["disconnected"], [])

    def test_dnp_series_part_is_open_by_intent(self):
        topo = self.series_board(dnp=True)
        self.assertEqual([r for r in topo["pairs"] if "through" in r], [])
        dnp = [d for d in topo["disconnected"]
               if d["pins"] == [["J1", "1"], ["U2", "1"]]]
        self.assertEqual(len(dnp), 1)
        self.assertIn("DNP", dnp[0]["reason"])

    def test_genuine_open_without_bridge(self):
        text = board(["SIG"],
                     seg(1, 0, 0, 4, 0) + "\n" + seg(1, 6, 0, 10, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("U2", 10, 0, [("1", 0, 0, 1, "SIG")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["SIG"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["SIG"]["pads"], Project("open"))
        self.assertEqual(topo["pairs"], [])
        self.assertEqual(len(topo["disconnected"]), 1)
        self.assertIn("copper open", topo["disconnected"][0]["reason"])

    def test_blind_via_does_not_join_other_layer(self):
        text = board(["LINK"],
                     seg(1, 0, 5, 5, 5) + "\n"
                     + '  (via (at 5 5) (size 0.8) (drill 0.4) (layers "F.Cu" "In1.Cu") (net 1))\n'
                     + seg(1, 5, 5, 9, 5, layer="B.Cu") + "\n" + via(1, 9, 5) + "\n"
                     + seg(1, 9, 5, 10, 5) + "\n"
                     + footprint("J1", 0, 5, [("1", 0, 0, 1, "LINK")]) + "\n"
                     + footprint("J2", 10, 5, [("1", 0, 0, 1, "LINK")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["LINK"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["LINK"]["pads"], Project("via"))
        # B.Cu span reaches J2 only via the F.Cu stack-up; the blind via cannot
        # carry it, so J1-J2 stay disconnected and no layer is fabricated.
        self.assertEqual(topo["span_mm"], 0.0)
        self.assertEqual(len(topo["disconnected"]), 1)

    def test_drawn_counts_branches_span_does_not(self):
        text = board(["SIG"],
                     seg(1, 0, 0, 10, 0) + "\n" + seg(1, 5, 0, 5, 5) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J3", 5, 5, [("1", 0, 0, 1, "SIG")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        self.assertAlmostEqual(ra._drawn_length(copper["nets"]["SIG"]), 15.0, places=3)


class PadReferenceTests(unittest.TestCase):
    def test_property_reference_fallback(self):
        text = board(["N1"],
                     seg(1, 0, 0, 5, 0) + "\n"
                     + '  (footprint "Test:R1" (layer "F.Cu") (tstamp "1") (at 0 0)\n'
                       '    (property "Reference" "R1") (property "Value" "10k")\n'
                       '    (pad "1" smd (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "N1"))\n'
                       "  )")
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        self.assertEqual([(p["reference"], p["pin"]) for p in copper["nets"]["N1"]["pads"]],
                         [("R1", "1")])


class AngleTests(unittest.TestCase):
    def test_acute_reported_obtuse_ignored(self):
        # BEND turns 63 deg (interior 117, obtuse, normal); SMOOTH turns back
        # 117 deg (interior 63, a sharp V). Straight copper is interior 180.
        text = board(["BEND", "SMOOTH"],
                     seg(1, 0, 10, 5, 10) + "\n" + seg(1, 5, 10, 6, 12) + "\n"
                     + seg(2, 0, 20, 5, 20) + "\n" + seg(2, 5, 20, 4, 22))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        by_net = {}
        for name in ("BEND", "SMOOTH"):
            graph = ra.build_graph(copper["nets"][name], copper["layers"])
            by_net[name] = ra._turn_angles(copper["nets"][name], graph)
        self.assertAlmostEqual(by_net["BEND"][0]["interior_deg"], 116.6, places=0)
        self.assertAlmostEqual(by_net["SMOOTH"][0]["interior_deg"], 63.4, places=0)
        acute = [b for name in ("BEND", "SMOOTH") for b in by_net[name]
                 if b["interior_deg"] < ra.ACUTE_TURN_DEG]
        self.assertEqual([round(b["interior_deg"]) for b in acute], [63])

    def test_fillet_scale_joints_excluded(self):
        text = board(["TEAR"],
                     seg(1, 0, 30, 5, 30) + "\n" + seg(1, 5, 30, 4.98, 30.05))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["TEAR"], copper["layers"])
        reportable, fillets = ra._reportable_bends(copper["nets"]["TEAR"], graph)
        self.assertEqual(reportable, [])
        self.assertEqual(fillets, 1)


class IntegrationTests(unittest.TestCase):
    def run_routing(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "t.kicad_pcb")
            path.write_text(text)
            project = Project("route", selected_files={"pcb": "t.kicad_pcb"})
            import hashlib as sha
            project.provenance["source_hashes"] = {"t.kicad_pcb": sha.sha256(text.encode()).hexdigest()}
            ra.analyze_routing(project, directory)
            return project

    def test_shared_tree_matches_fresh_parse(self):
        # One verified read+parse shared by both copper passes must give
        # byte-identical results to per-pass parsing.
        from pcb_checker import signal_paths as spaths
        from pcb_checker.parsers.sexpr import parse as parse_tree
        with tempfile.TemporaryDirectory() as directory:
            raw = USB_BOARD.encode()
            Path(directory, "t.kicad_pcb").write_bytes(raw)
            import hashlib as sha
            digest = sha.sha256(raw).hexdigest()

            def blank():
                project = Project("route", selected_files={"pcb": "t.kicad_pcb"})
                project.provenance["source_hashes"] = {"t.kicad_pcb": digest}
                return project
            fresh = blank()
            spaths.analyze_signal_paths(fresh, directory)
            ra.analyze_routing(fresh, directory)
            tree = parse_tree(USB_BOARD, "kicad_pcb")
            shared = blank()
            spaths.analyze_signal_paths(shared, directory, pcb_tree=tree, pcb_raw=raw)
            ra.analyze_routing(shared, directory, pcb_tree=tree)
            self.assertEqual(shared.signal_paths, fresh.signal_paths)
            self.assertEqual(shared.routing, fresh.routing)
            self.assertEqual([i.code for i in shared.issues], [i.code for i in fresh.issues])

    def test_pair_group_reaches_evidence(self):
        project = self.run_routing(USB_BOARD)
        groups = [g for g in project.review["groups"] if g["title"].startswith("Differential pair")]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["category"], "signal")
        self.assertEqual(groups[0]["priority"], "review")
        from pcb_checker.ai.reviewer import evidence_packet
        packet = evidence_packet(project)
        self.assertIn(groups[0]["id"], packet["action_group_ids"])
        self.assertIn("2.0", groups[0]["summary"])

    def test_pair_bend_locations_reach_evidence(self):
        text = board(["TX_P", "TX_N"],
                     seg(1, 0, 0, 5, 0) + "\n" + seg(1, 5, 0, 4, 2) + "\n"
                     + seg(2, 0, 1, 8, 1) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "TX_P"), ("2", 0, 1, 2, "TX_N")]) + "\n"
                     + footprint("J2", 8, 0, [("1", -4, 0, 1, "TX_P"), ("2", 0, 1, 2, "TX_N")]))
        project = self.run_routing(text)
        pair_issue = next(i for i in project.issues if i.code == "ROUTING_DIFF_PAIR")
        joined = " ".join(pair_issue.evidence)
        self.assertIn("pair bend on TX_P", joined)
        self.assertIn("63.4", joined)

    def test_pin_cap_is_disclosed_in_summary(self):
        pads = "\\n".join(footprint(f"J{i}", i * 2, 0, [("1", 0, 0, 1, "BIG")]) for i in range(70))
        project = self.run_routing(board(["BIG"], seg(1, 0, 0, 2, 0) + "\\n" + pads))
        summary = next(i for i in project.issues if i.code == "ROUTING_SUMMARY")
        self.assertIn("Partial coverage", summary.message)
        self.assertIn("BIG", summary.message)

    def test_summary_group_records_scope(self):
        project = self.run_routing(USB_BOARD)
        codes = {project.issues[int(fid[1:]) - 1].code
                 for g in project.review["groups"] for fid in g["issue_ids"]
                 if fid.startswith("F") and fid[1:].isdigit()}
        self.assertIn("ROUTING_SUMMARY", codes)


def zone(net_id, net_name, layer_polys):
    parts = [f'  (zone (net {net_id}) (net_name "{net_name}") (layers "F.Cu" "B.Cu")']
    for layer, pts in layer_polys:
        coords = " ".join(f"(xy {x} {y})" for x, y in pts)
        parts.append(f'    (filled_polygon (layer "{layer}") (pts {coords}))')
    parts.append("  )")
    return "\\n".join(parts)


class PourVerificationTests(unittest.TestCase):
    def topo_of(self, text, net="GND"):
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"][net], copper["layers"])
        return ra.pin_topology(graph, copper["nets"][net]["pads"], Project("pour"),
                               copper.get("zones"), copper["nets"][net]["vias"])

    def test_shared_fill_joins_trace_islands(self):
        text = board(["GND"],
                     footprint("J1", 0, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + zone(1, "GND", [("F.Cu", [(-2, -2), (12, -2), (12, 2), (-2, 2)])]))
        topo = self.topo_of(text)
        self.assertEqual(topo["disconnected"], [])
        self.assertEqual(len(topo["pour_joined"]), 1)
        self.assertIn("GND F.Cu fill 0", topo["pour_joined"][0]["detail"])

    def test_separate_fills_stay_open(self):
        text = board(["GND"],
                     footprint("J1", 0, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + zone(1, "GND", [("F.Cu", [(-2, -2), (2, -2), (2, 2), (-2, 2)]),
                                       ("F.Cu", [(8, -2), (12, -2), (12, 2), (8, 2)])]))
        topo = self.topo_of(text)
        self.assertEqual(topo["pour_joined"], [])
        self.assertEqual(len(topo["disconnected"]), 1)
        self.assertIn("separate fill islands", topo["disconnected"][0]["reason"])

    def test_stitching_via_joins_fills_across_layers(self):
        text = board(["GND"],
                     tht_footprint("J1", 0, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + tht_footprint("J2", 10, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + via(1, 5, 0) + "\\n"
                     + zone(1, "GND", [("F.Cu", [(-2, -2), (7, -2), (7, 2), (-2, 2)]),
                                       ("B.Cu", [(3, -2), (12, -2), (12, 2), (3, 2)])]))
        topo = self.topo_of(text)
        self.assertEqual(topo["disconnected"], [])
        self.assertEqual(len(topo["pour_joined"]), 1)

    def test_pad_near_but_outside_fill_stays_open(self):
        text = board(["GND"],
                     footprint("J1", 0, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "GND")]) + "\\n"
                     + zone(1, "GND", [("F.Cu", [(-2, -2), (2, -2), (2, 2), (-2, 2)])]))
        topo = self.topo_of(text)
        # J1 sits in the fill, J2 misses it entirely: still one open entry.
        self.assertEqual(len(topo["disconnected"]), 1)
        self.assertEqual(topo["pour_joined"], [])


def tht_footprint(ref, x, y, pads, ptype="thru_hole"):
    lines = [f'  (footprint "Test:{ref}" (layer "F.Cu") (tstamp "{ref}") (at {x} {y})',
             f'    (fp_text reference "{ref}" (at 0 0))',
             f'    (fp_text value "v" (at 0 1))']
    for number, dx, dy, net_id, net_name in pads:
        lines.append(f'    (pad "{number}" {ptype} (at {dx} {dy}) (size 1.7 1.7) (drill 1.0) '
                     f'(layers "*.Cu" "*.Mask") (net {net_id} "{net_name}"))')
    lines.append("  )")
    return "\n".join(lines)


class PlatedBarrelTests(unittest.TestCase):
    def barrel_board(self, ptype="thru_hole"):
        # J4 sits exactly at the F.Cu/B.Cu joint: F.Cu reaches it from J7,
        # B.Cu leaves it toward J8. Only a plated barrel completes J7-J8.
        return board(["PWR"],
                     seg(1, 0, 0, 5, 0) + "\n"
                     + seg(1, 5, 0, 10, 0, layer="B.Cu") + "\n"
                     + tht_footprint("J4", 5, 0, [("1", 0, 0, 1, "PWR")], ptype=ptype) + "\n"
                     + tht_footprint("J7", 0, 0, [("1", 0, 0, 1, "PWR")], ptype=ptype) + "\n"
                     + tht_footprint("J8", 10, 0, [("1", 0, 0, 1, "PWR")], ptype=ptype))

    def test_plated_thru_hole_joins_copper_layers(self):
        copper = ra.extract_copper(parse(self.barrel_board(), "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["PWR"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["PWR"]["pads"], Project("barrel"))
        self.assertEqual(topo["disconnected"], [])
        long_route = [r for r in topo["pairs"] if r["pins"] == [["J7", "1"], ["J8", "1"]]]
        self.assertEqual(len(long_route), 1)
        self.assertAlmostEqual(long_route[0]["length_mm"], 10.0, places=3)

    def test_npth_hole_does_not_join_layers(self):
        copper = ra.extract_copper(parse(self.barrel_board(ptype="np_thru_hole"), "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["PWR"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["PWR"]["pads"], Project("npth"))
        self.assertEqual([r for r in topo["pairs"] if r["pins"] == [["J7", "1"], ["J8", "1"]]], [])
        self.assertEqual(len(topo["disconnected"]), 1)
        self.assertIn("J8-1", topo["disconnected"][0]["reason"])


class CrossNetBridgeTests(unittest.TestCase):
    def cross_net_board(self):
        return board(["D+", "DPX"],
                     seg(1, 0, 0, 4, 0) + "\n" + seg(2, 6, 0, 10, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "D+")]) + "\n"
                     + footprint("R5", 4, 0, [("1", 0, 0, 1, "D+"), ("2", 2, 0, 2, "DPX")]) + "\n"
                     + footprint("U2", 10, 0, [("47", 0, 0, 2, "DPX")]))

    def project_with(self, dnp):
        project = Project("xnet")
        rec = ComponentRecord("R5", "22", "pkg", "s.kicad_sch", 1, dnp=dnp)
        project.components["R5"] = Component("R5", [rec], [], [])
        return project

    def test_series_part_joins_nets_electrically(self):
        copper = ra.extract_copper(parse(self.cross_net_board(), "kicad_pcb"))
        groups = ra.electrical_net_groups(copper, self.project_with(False))
        self.assertIn(sorted(["D+", "DPX"]), [sorted(g) for g in groups])
        merged = ra.merged_group_items(copper, ["D+", "DPX"])
        graph = ra.build_graph(merged, copper["layers"])
        topo = ra.pin_topology(graph, merged["pads"], self.project_with(False))
        routed = [r for r in topo["pairs"]
                  if r["pins"] == [["J1", "1"], ["U2", "47"]] and "through" in r]
        self.assertEqual(len(routed), 1)
        self.assertEqual(routed[0]["through"], [{"reference": "R5", "value": "22"}])
        self.assertAlmostEqual(routed[0]["length_mm"], 8.0, places=3)

    def test_dnp_series_part_keeps_nets_split(self):
        copper = ra.extract_copper(parse(self.cross_net_board(), "kicad_pcb"))
        project = self.project_with(True)
        groups = ra.electrical_net_groups(copper, project)
        self.assertNotIn(sorted(["D+", "DPX"]), [sorted(g) for g in groups])
        merged = ra.merged_group_items(copper, ["D+", "DPX"])
        graph = ra.build_graph(merged, copper["layers"])
        topo = ra.pin_topology(graph, merged["pads"], project)
        self.assertEqual([r for r in topo["pairs"] if "through" in r], [])
        dnp = [d for d in topo["disconnected"] if "DNP" in d["reason"]]
        self.assertTrue(dnp)

    def test_connector_across_nets_never_bridges(self):
        text = board(["A", "B"],
                     footprint("J1", 0, 0, [("1", 0, 0, 1, "A"), ("2", 0, 2, 2, "B")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        groups = ra.electrical_net_groups(copper, Project("conn"))
        self.assertEqual(groups, [])

    def test_decoupling_capacitor_never_merges_rail_and_ground(self):
        text = board(["VCC", "GND"],
                     seg(1, 0, 0, 5, 0) + "\n" + seg(1, 5, 0, 10, 0) + "\n"
                     + seg(2, 0, 2, 5, 2) + "\n" + seg(2, 5, 2, 10, 2) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "VCC")]) + "\n"
                     + footprint("J2", 0, 2, [("1", 0, 0, 2, "GND")]) + "\n"
                     + footprint("C1", 5, 0, [("1", 0, 0, 1, "VCC"), ("2", 0, 2, 2, "GND")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        project = Project("decap")
        rec = ComponentRecord("C1", "100n", "pkg", "s.kicad_sch", 1, dnp=False)
        project.components["C1"] = Component("C1", [rec], [], [])
        self.assertEqual(ra.electrical_net_groups(copper, project), [])
        self.assertEqual(ra.series_net_sets(copper, project, fitted_only=False), [])
        for net in ("VCC", "GND"):
            graph = ra.build_graph(copper["nets"][net], copper["layers"])
            topo = ra.pin_topology(graph, copper["nets"][net]["pads"], project)
            self.assertEqual(topo["disconnected"], [])


class DisconnectedNoiseTests(unittest.TestCase):
    def test_singleton_pins_report_once_not_pairwise(self):
        text = board(["PWR"],
                     seg(1, 0, 0, 10, 0) + "\n" + seg(1, 10, 0, 20, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "PWR")]) + "\n"
                     + footprint("J2", 10, 0, [("1", 0, 0, 1, "PWR")]) + "\n"
                     + footprint("J3", 20, 0, [("1", 0, 0, 1, "PWR")]) + "\n"
                     + footprint("C1", 0, 5, [("1", 0, 0, 1, "PWR")]) + "\n"
                     + footprint("C2", 5, 5, [("1", 0, 0, 1, "PWR")]) + "\n"
                     + footprint("C3", 10, 5, [("1", 0, 0, 1, "PWR")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["PWR"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["PWR"]["pads"], Project("noise"))
        self.assertEqual(len(topo["disconnected"]), 3)
        mentions = " ".join(d["reason"] for d in topo["disconnected"])
        for ref in ("C1-1", "C2-1", "C3-1"):
            self.assertIn(ref, mentions)

    def test_split_copper_reports_one_entry_per_island_pair(self):
        text = board(["SIG"],
                     seg(1, 0, 0, 5, 0) + "\n" + seg(1, 10, 0, 15, 0) + "\n"
                     + footprint("J1", 0, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J2", 5, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J3", 10, 0, [("1", 0, 0, 1, "SIG")]) + "\n"
                     + footprint("J4", 15, 0, [("1", 0, 0, 1, "SIG")]))
        copper = ra.extract_copper(parse(text, "kicad_pcb"))
        graph = ra.build_graph(copper["nets"]["SIG"], copper["layers"])
        topo = ra.pin_topology(graph, copper["nets"]["SIG"]["pads"], Project("split"))
        self.assertEqual(len(topo["disconnected"]), 1)


if __name__ == "__main__":
    unittest.main()
