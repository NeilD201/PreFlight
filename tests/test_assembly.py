"""Assembly mismatch detection must preserve uncertainty, DNP intent and provenance."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.analysis import analyze_project
from pcb_checker.parsers.assembly import CSVError, coordinate, parse_assembly, references
from pcb_checker.models import Component, ComponentRecord, Pad, Project
from pcb_checker.checks.assembly import check_assembly, parts_inventory
from pcb_checker.report import save_report
from test_components import schematic, symbol, board, footprint


def parse(text,kind='bom',units='auto'):
    return parse_assembly(text.encode(),kind+'.csv',kind,units=units)


def sample_board():
    p=Project('board')
    for ref in ('C1','C2'):
        p.components[ref]=Component(ref,pcb=[ComponentRecord(ref,'100n','Capacitor_SMD:C_0402_1005Metric','board.kicad_pcb',1,
            pads=[Pad('1','GND',2),Pad('2','VCC',3)],attributes=['smd'],layer='F.Cu')])
    return p


class AssemblyParserTests(unittest.TestCase):
    def test_groups_ranges_and_semicolons(self):
        self.assertEqual(references('C1, C2 C4-C6; R10'),['C1','C2','C4','C5','C6','R10'])
        result=parse('Designator;Value;Footprint;Quantity;MPN\n"C1,C2";100n;0402;2;part-id\n')
        self.assertEqual(len(result['records']),2)
        self.assertEqual(result['records'][1]['mpn'],'part-id')
        self.assertEqual(result['records'][1]['line'],2)
        self.assertEqual(result['status'],'parsed')

    def test_bad_ranges_and_junk_are_not_silently_discarded(self):
        for value in ('C1-C100000000','C8-C1','C1-R3','C1?','C1 / ???',''):
            with self.subTest(value=value),self.assertRaises(CSVError):references(value)

    def test_duplicate_headers_and_ambiguous_mapping(self):
        for text in ('Reference,reference,Value\nC1,C2,100n\n','Reference,Ref,Value\nC1,C2,100n\n','Value,Footprint\n1,0402\n'):
            with self.assertRaises(CSVError):parse(text)

    def test_coordinate_units_are_explicit_and_conflicts_rejected(self):
        self.assertEqual(coordinate('1in','X')[2],25.4)
        self.assertEqual(coordinate('1,25','X(mm)')[2],1.25)
        self.assertEqual(coordinate('20','X')[2],None)
        self.assertEqual(coordinate('-2','X','mm')[2],-2)
        for args in [('NaN','X'),('inf','X'),('1e999','X'),('2in','X(mm)'),('4mm','X','in')]:
            with self.assertRaises(ValueError):coordinate(*args)

    def test_cpl_invalid_fields_and_grouped_designators(self):
        result=parse('Designator,Mid X,Mid Y,Layer,Rotation\nU1,nan,2,sideways,banana\n"C1,C2",1,2,Top,0\n','cpl')
        codes={i.code for i in result['issues']}
        self.assertTrue({'ASSEMBLY_INVALID_COORDINATE','ASSEMBLY_INVALID_LAYER','ASSEMBLY_INVALID_ROTATION','ASSEMBLY_INVALID_REFERENCE'}<=codes)
        self.assertFalse(result['records'][0]['valid'])

    def test_native_headers_mm_rotation_and_side(self):
        result=parse('Ref,Val,Package,PosX,PosY,Rot,Side\nU1,chip,DFN,3,-5,-90,bottom\n','cpl','mm')
        r=result['records'][0]
        self.assertEqual((r['x_mm'],r['y_mm'],r['rotation'],r['layer']),(3,-5,270,'bottom'))
        self.assertFalse(result['issues'])

    def test_dnp_quantity_zero_and_unknown_flags(self):
        result=parse('Designator,Value,DNP,Quantity\nR1,10k,yes,0\n')
        self.assertTrue(result['records'][0]['dnp']);self.assertEqual(result['status'],'parsed')
        bad=parse('Designator,Value,DNP\nR1,10k,maybe\n')
        self.assertFalse(bad['records'][0]['valid'])

    def test_row_width_and_quantity_errors(self):
        result=parse('Designator,Value,Quantity\nC1,100n,2\nC2,100n,1,extra\n')
        self.assertEqual({i.code for i in result['issues']},{'ASSEMBLY_QUANTITY_MISMATCH','ASSEMBLY_ROW_WIDTH'})


class AssemblyCheckTests(unittest.TestCase):
    def check(self,bom,cpl=None,p=None):
        p=p or sample_board();parsed={'bom':parse(bom)}
        if cpl is not None:parsed['cpl']=parse(cpl,'cpl')
        check_assembly(p,parsed,pcb_ready=True)
        return p,{i.code for i in p.issues}

    def test_equal_passive_values_and_complete_exports(self):
        p,codes=self.check('Designator,Value,Footprint\n"C1,C2",0.1uF,0402\n',
            'Designator,X(mm),Y(mm),Layer,Rotation\nC1,10,20,Top,0\nC2,15,25,Top,90\n')
        self.assertFalse(codes)
        self.assertEqual(p.assembly['status'],'compared')
        self.assertEqual(len(p.components['C1'].bom),1)
        self.assertEqual(len(p.components['C1'].cpl),1)

    def test_package_side_and_unknown_pcb_reference_are_found(self):
        p,codes=self.check('Designator,Value,Footprint\nC1,100n,0603\nU99,chip,DFN\n',
            'Designator,X(mm),Y(mm),Layer,Rotation\nC1,1,2,Bottom,0\n')
        self.assertTrue({'ASSEMBLY_PACKAGE_MISMATCH','ASSEMBLY_LAYER_MISMATCH','ASSEMBLY_REFERENCE_NOT_ON_PCB','ASSEMBLY_PCB_MISSING_FROM_BOM','ASSEMBLY_MISSING_PLACEMENT'}<=codes)
        self.assertIn('U99',p.components)

    def test_dnp_mechanical_and_manual_placement_are_not_missing_part_errors(self):
        p=sample_board();p.components['C2'].pcb[0].dnp=True
        p.components['H1']=Component('H1',pcb=[ComponentRecord('H1','hole','MountingHole:MountingHole_3mm','b',2)])
        p.components['J1']=Component('J1',pcb=[ComponentRecord('J1','connector','Connector:TH','b',3,attributes=['through_hole'])])
        _,codes=self.check('Designator,Value,Footprint\nC1,100n,0402\nJ1,connector,Connector:TH\n',
            'Designator,X(mm),Y(mm),Layer,Rotation\nC1,1,2,Top,0\n',p)
        self.assertFalse(codes)

    def test_bom_dnp_in_placement_requires_review(self):
        _,codes=self.check('Designator,Value,DNP\nC1,100n,yes\nC2,100n,no\n',
            'Designator,X(mm),Y(mm),Layer,Rotation\nC1,1,2,Top,0\nC2,2,2,Top,0\n')
        self.assertIn('ASSEMBLY_DNP_IN_PLACEMENT',codes)

    def test_duplicate_references_never_overwrite_records(self):
        p,codes=self.check('Designator,Value\nC1,100n\nC1,1u\n')
        self.assertIn('ASSEMBLY_DUPLICATE_REFERENCE',codes)
        self.assertEqual(len(p.components['C1'].bom),2)

    def test_unspecified_units_are_reported_without_invented_coordinates(self):
        p,codes=self.check('Designator,Value\n"C1,C2",100n\n',
            'Designator,X,Y,Layer,Rotation\nC1,1,2,Top,0\nC2,2,2,Top,0\n')
        self.assertIn('ASSEMBLY_UNITS_NOT_CONFIRMED',codes)
        self.assertIsNone(p.components['C1'].cpl[0]['x_mm'])

    def test_no_pcb_does_not_claim_assembly_comparison(self):
        p=Project('csv');check_assembly(p,{'bom':parse('Designator,Value\nU1,chip\n')},pcb_ready=False)
        self.assertEqual(p.assembly['status'],'partial')
        self.assertFalse(any(i.code=='ASSEMBLY_REFERENCE_NOT_ON_PCB' for i in p.issues))

    def test_part_identity_conflicts_are_not_silently_resolved(self):
        p=sample_board();p.components['C1'].pcb[0].properties={'MPN':'part-a'}
        self.check('Designator,Value,MPN\nC1,100n,part-b\nC2,100n,part-c\n',p=p)
        parts=parts_inventory(p)
        self.assertEqual(parts[0]['status'],'conflicting_identity');self.assertEqual(parts[0]['mpn'],'')
        self.assertEqual(parts[1]['mpn'],'part-c')


class AssemblyIntegrationTests(unittest.TestCase):
    def test_real_import_merge_report_and_input_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            source=Path(td)/'input';source.mkdir()
            (source/'demo.kicad_sch').write_text(schematic(symbol()))
            (source/'demo.kicad_pcb').write_text(board(footprint()))
            (source/'bom.csv').write_text('Designator,Value,Footprint,MPN\nC1,100n,0402,part-one\nU99,chip,DFN,part-two\n')
            (source/'cpl.csv').write_text('Designator,X(mm),Y(mm),Layer,Rotation\nC1,1,2,Top,0\n')
            p=analyze_project(source)
            self.assertEqual(p.components['C1'].bom[0]['mpn'],'part-one')
            self.assertIn('ASSEMBLY_REFERENCE_NOT_ON_PCB',{i.code for i in p.issues})
            self.assertEqual(len(p.provenance['source_hashes']),4)
            out=save_report(p,Path(td)/'reports')
            self.assertTrue((out/'report.html').is_file())
            self.assertIn('U99',(out/'report.json').read_text())

    def test_multiple_boms_require_explicit_selection(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            for file in ('one.csv','two.csv'):(root/file).write_text('Designator,Value\nR1,10k\n')
            p=analyze_project(root)
            self.assertIn('SELECT_BOM_CANDIDATE_FILE',{i.code for i in p.issues})
            self.assertFalse(p.assembly['bom_count'])
            selected=analyze_project(root,bom='one.csv')
            self.assertEqual(selected.assembly['bom_count'],1)


if __name__=='__main__':unittest.main()
