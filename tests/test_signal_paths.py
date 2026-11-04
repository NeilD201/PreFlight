import hashlib
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.models import Component, ComponentRecord, Pad, Pin, Project
from pcb_checker.signal_paths import analyze_signal_paths


BOARD = '''(kicad_pcb
 (net 1 "FEED") (net 2 "OUT") (net 3 "GND")
 (segment (start 1 1) (end 2 1) (width 0.15) (layer "F.Cu") (net 1))
 (segment (start 2 1) (end 3 1) (width 0.15) (layer "F.Cu") (net 2))
)'''


def part(ref, value, pads, pin=None):
    record = ComponentRecord(ref, value, value, 'board.kicad_pcb', 1,
                             pads=[Pad(number, net, 1) for number, net in pads])
    return Component(ref, [record], [record], [pin] if pin else [])


class SignalPathTests(unittest.TestCase):
    def test_series_selection_ignores_shunts_dnp_and_ambiguous_branches(self):
        from pcb_checker.signal_paths import _series_neighbor
        self.project.components['R99']=part('R99','50',[('1','FEED'),('2','GND')])
        self.assertEqual(_series_neighbor(self.project,'FEED'),('L1','OUT'))
        self.project.components['R23']=part('R23','0',[('1','FEED'),('2','OTHER')])
        self.assertEqual(_series_neighbor(self.project,'FEED'),(None,''))
        self.project.components['R23'].pcb[0].dnp=True
        self.assertEqual(_series_neighbor(self.project,'FEED'),('L1','OUT'))
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.file = self.folder / 'board.kicad_pcb'
        self.file.write_text(BOARD)
        self.project = Project('example')
        self.project.selected_files['pcb'] = self.file.name
        self.project.provenance['source_hashes'] = {self.file.name: hashlib.sha256(BOARD.encode()).hexdigest()}
        self.project.components = {
            'U1': part('U1', 'nRF52840', [('H23', 'FEED')], Pin('H23', 'ANT', net='FEED')),
            'L1': part('L1', '4.7nH', [('1', 'FEED'), ('2', 'OUT')]),
            'C1': part('C1', '0.5pF', [('1', 'OUT'), ('2', 'GND')]),
        }

    def test_missing_antenna_is_review_with_measured_copper(self):
        analyze_signal_paths(self.project, self.folder)
        issue = next(i for i in self.project.issues if i.code == 'RF_FEED_NO_TERMINAL')
        self.assertEqual(issue.component, 'U1')
        self.assertEqual([n['copper_mm'] for n in self.project.signal_paths['paths'][0]['nets']], [1.0, 1.0])
        self.assertTrue(any(g['signal_path_id'] == 'rf-U1' and g['category'] == 'signal'
                            for g in self.project.review['groups']))

    def test_named_antenna_suppresses_missing_terminal(self):
        self.project.components['AE1'] = part('AE1', '2.4GHz Antenna', [('1', 'OUT')])
        analyze_signal_paths(self.project, self.folder)
        self.assertFalse(any(i.code == 'RF_FEED_NO_TERMINAL' for i in self.project.issues))

    def test_unnetted_copper_drawing_outside_edge_cuts_is_marked_separately_from_rf_output(self):
        text='''(kicad_pcb
          (net 1 "FEED") (net 2 "OUT") (net 3 "GND")
          (segment (start 1 1) (end 2 1) (width 0.15) (layer "F.Cu") (net 1) (uuid "feed-track"))
          (segment (start 2 1) (end 3 1) (width 0.15) (layer "F.Cu") (net 2) (uuid "out-track"))
          (gr_line (start 0 0) (end 10 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e1"))
          (gr_line (start 10 0) (end 10 -10) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e2"))
          (gr_line (start 10 -10) (end 0 -10) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e3"))
          (gr_line (start 0 -10) (end 0 0) (stroke (width 0.05) (type default)) (layer "Edge.Cuts") (uuid "e4"))
          (gr_line (start 20 -5) (end 22 -4) (stroke (width 0.2) (type default)) (layer "F.Cu") (uuid "loose-copper"))
        )'''
        self.file.write_text(text)
        self.project.provenance['source_hashes'][self.file.name] = hashlib.sha256(text.encode()).hexdigest()
        analyze_signal_paths(self.project,self.folder)
        path=self.project.signal_paths['paths'][0]
        self.assertEqual(path['unnetted_copper_outside_outline'][0]['uuid'],'loose-copper')
        issue=next(i for i in self.project.issues if i.code=='RF_FEED_NO_TERMINAL')
        self.assertIn('outside the board outline',issue.message)
        self.assertIn('loose-copper',issue.evidence[2])

    def test_changed_board_is_not_measured(self):
        self.file.write_text(BOARD + '\n')
        analyze_signal_paths(self.project, self.folder)
        self.assertEqual(self.project.signal_paths['status'], 'unavailable')
        self.assertFalse(any(i.source == 'signal_path' for i in self.project.issues))

    def test_ecg_review_reports_vias_without_claiming_measured_noise(self):
        text = '''(kicad_pcb
          (net 1 "ECGP") (net 2 "ECGN")
          (segment (start 0 0) (end 1 0) (width .2) (layer "F.Cu") (net 1))
          (segment (start 0 1) (end 1 1) (width .2) (layer "F.Cu") (net 2))
          (via (at 0 0) (net 1)) (via (at 1 0) (net 1)) (via (at 2 0) (net 1))
        )'''
        self.file.write_text(text)
        self.project.provenance['source_hashes'][self.file.name] = hashlib.sha256(text.encode()).hexdigest()
        self.project.components = {'U2': part('U2', 'MAX30001', [('A5', 'ECGP'), ('A6', 'ECGN')])}
        self.project.components['U2'].schematic_pins = [Pin('A5', 'ECGP', net='ECGP'), Pin('A6', 'ECGN', net='ECGN')]
        analyze_signal_paths(self.project, self.folder)
        issue = next(i for i in self.project.issues if i.code == 'SENSITIVE_INPUT_ROUTE_REVIEW')
        self.assertIn('not a measured noise', issue.message)
        self.assertEqual(issue.severity,'info')
        group=next(g for g in self.project.review['groups'] if g.get('signal_path_id')=='ecg-U2')
        self.assertEqual(group['priority'],'note')
        self.assertTrue(group['no_change_basis'])
        self.assertEqual(self.project.signal_paths['paths'][0]['nets'][0]['vias'], 3)


if __name__ == '__main__':
    unittest.main()
