import csv
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.assembly_export import assembly_files, assembly_bundle, generate_assembly, placement_snapshot
from pcb_checker.datasheets import LCSCClient, candidate_choice, collect_datasheets, DatasheetError, part_signature, footprint_part_hint
from pcb_checker.design_review import edit_context, empty_context
from pcb_checker.models import Component, ComponentRecord, Project
from pcb_checker.webapp import AppState


def fixture():
    p=Project('assembly-test',selected_files={'pcb':'board.kicad_pcb'},provenance={'source_hashes':{'board.kicad_pcb':'abc'}})
    for ref in ['R1','R2','R3','R4']:
        p.components[ref]=Component(ref,pcb=[ComponentRecord(ref,'10k','Resistor_SMD:R_0402_1005Metric','board.kicad_pcb',1)])
    p.components['R3'].pcb[0].dnp=True
    p.components['R4'].pcb[0].attributes=['exclude_from_pos_files']
    p.datasheets={'parts':[{'reference':ref,'code':'C25744','status':'provisional','match':'value_package_candidate','mpn':'TEST'} for ref in ['R1','R2','R4']]}
    p.assembly_export={'placement':{'status':'generated','pcb_sha256':'abc','rows':[
        {'reference':ref,'x_mm':-12.5,'y_mm':3.25,'rotation':270.0,'side':'bottom'} for ref in ['R1','R2','R3','R4']]}}
    return p


class ExportTests(unittest.TestCase):
    def test_grouping_population_and_placement_flags(self):
        p=fixture();p.assembly_export=generate_assembly(p)
        self.assertEqual(p.assembly_export['counts']['fitted'],3)
        self.assertEqual(p.assembly_export['bom'][0]['references'],['R1','R2','R4'])
        self.assertEqual([r['reference'] for r in p.assembly_export['cpl']],['R1','R2'])
        self.assertEqual(p.assembly_export['counts']['missing_placements'],0)
        rows=list(csv.DictReader(io.StringIO(assembly_files(asdict(p))['bom-draft.csv'].decode('utf-8-sig'))))
        self.assertEqual(rows[0]['LCSC Part #'],'C25744')
        self.assertEqual(rows[0]['Designator'],'R1,R2,R4')

    def test_negative_cpl_coordinates_remain_numeric_and_text_is_escaped(self):
        p=fixture();p.components['R1'].pcb[0].value='=bad()';p.assembly_export=generate_assembly(p)
        files=assembly_files(asdict(p));rows=list(csv.DictReader(io.StringIO(files['cpl-draft.csv'].decode('utf-8-sig'))))
        self.assertEqual(float(rows[0]['Mid X']),-12.5)
        self.assertEqual(rows[0]['Layer'],'bottom');self.assertEqual(float(rows[0]['Rotation']),270)
        self.assertIn("'=bad()",files['bom-draft.csv'].decode())

    def test_missing_part_does_not_silently_disappear_from_bom(self):
        p=fixture();p.datasheets={};p.assembly_export=generate_assembly(p)
        self.assertEqual(p.assembly_export['counts']['unresolved'],3)
        self.assertEqual(sum(len(b['references']) for b in p.assembly_export['bom']),3)
        self.assertEqual(p.assembly_export['bom'][0]['code'],'')

    def test_duplicate_and_case_colliding_references_block_machine_files(self):
        for duplicate in [True,False]:
            p=fixture()
            if duplicate:p.components['R1'].pcb.append(p.components['R1'].pcb[0])
            else:p.components['r1']=p.components['R1']
            p.assembly_export=generate_assembly(p)
            files=assembly_files(asdict(p))
            self.assertEqual(p.assembly_export['status'],'blocked')
            self.assertNotIn('bom-draft.csv',files);self.assertNotIn('cpl-draft.csv',files)

    def test_bundle_checksums_and_snapshot_reuse_without_source(self):
        p=fixture()
        with patch('pcb_checker.assembly_export.placement_snapshot') as export:p.assembly_export=generate_assembly(p,'deleted-upload.zip')
        export.assert_not_called()
        with zipfile.ZipFile(io.BytesIO(assembly_bundle(asdict(p)))) as z:
            for name,digest in json.loads(z.read('assembly-manifest.json'))['files'].items():
                self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
            self.assertIn('cpl-draft.csv',z.namelist())

    def test_changed_source_is_rejected_before_kicad_runs(self):
        with tempfile.TemporaryDirectory() as td:
            p=fixture();Path(td,'board.kicad_pcb').write_text('changed')
            with patch('pcb_checker.assembly_export.find_kicad',return_value='/kicad'),patch('pcb_checker.assembly_export._run') as run:
                value=placement_snapshot(p,td)
            run.assert_not_called();self.assertEqual(value['status'],'failed');self.assertIn('changed',value['message'])

    def test_native_command_and_both_side_coordinate_import(self):
        with tempfile.TemporaryDirectory() as td:
            p=fixture();raw=b'(kicad_pcb)';Path(td,'board.kicad_pcb').write_bytes(raw)
            p.provenance['source_hashes']['board.kicad_pcb']=hashlib.sha256(raw).hexdigest()
            commands=[]
            def run(binary,args,root,**kw):
                commands.append(args)
                if args==['version']:return '9.0.5'
                Path(args[args.index('--output')+1]).write_text('Ref,PosX,PosY,Rot,Side\nR1,-1.25,4,-90,bottom\nR2,2,3,90,top\n')
            with patch('pcb_checker.assembly_export.find_kicad',return_value='/kicad'),patch('pcb_checker.assembly_export._run',side_effect=run):
                result=placement_snapshot(p,td)
            self.assertEqual(result['status'],'generated')
            self.assertEqual(result['rows'][0]['rotation'],270)
            self.assertIn('--exclude-dnp',commands[1]);self.assertIn('--use-drill-file-origin',commands[1])
            self.assertEqual(commands[1][commands[1].index('--units')+1],'mm')
            self.assertEqual(Path(td,'board.kicad_pcb').read_bytes(),raw)

    def test_no_kicad_keeps_bom_and_explains_missing_cpl(self):
        p=fixture();p.assembly_export={}
        with patch('pcb_checker.assembly_export.find_kicad',return_value=None):p.assembly_export=generate_assembly(p,'source')
        files=assembly_files(asdict(p));self.assertIn('bom-draft.csv',files);self.assertNotIn('cpl-draft.csv',files)
        self.assertIn('KiCad',p.assembly_export['placement']['message'])


class AutomaticSourcingTests(unittest.TestCase):
    def test_passives_match_parameters_not_search_rank(self):
        def candidate(code,cap,package):return {'productCode':code,'productModel':'MODEL-'+code,'encapStandard':package,'paramVOList':[{'paramNameEn':'Capacitance','paramValueEn':cap}]}
        response={'code':200,'result':{'productList':[candidate('C1111','10nF','0402'),candidate('C2222','100nF','0603'),candidate('C3333','100nF','0402')]}}
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.datasheets.fetch',return_value=json.dumps(response).encode()) as fetch:
            client=LCSCClient(td);result=client.passive_search('C1','0.1uF','Capacitor_SMD:C_0402_1005Metric')
            self.assertEqual([p['code'] for p in result],['C3333'])
            client.passive_search('C2','100nF','Capacitor_SMD:C_0402_1005Metric');fetch.assert_called_once()

    def test_family_selection_rejects_wrong_package(self):
        parts=[{'code':'C1111','mpn':'MCP73831-2ACI/OT','package':'SOT-23-5'},
               {'code':'C2222','mpn':'MCP73831-2ACI/MC','package':'TDFN-8-EP(2x3)'}]
        chosen,status=candidate_choice('U1','MCP73831-2-MC','Package_DFN_QFN:DFN-8-1EP_3x2mm',parts)
        self.assertEqual(chosen['code'],'C2222');self.assertEqual(status,'automatic_candidate')

    def test_development_board_and_wrong_pin_count_are_not_ic_candidates(self):
        parts=[{'code':'C1111','mpn':'NRF52840-DONGLE','package':'-'},
               {'code':'C2222','mpn':'NRF52840-CKAA','package':'WLCSP-94'},
               {'code':'C3333','mpn':'NRF52840-QIAA-R','package':'AQFN-73'}]
        chosen,_=candidate_choice('U1','nRF52840','Nordic_AQFN-73-1EP',parts,74)
        self.assertEqual(chosen['code'],'C3333')
        parts=[{'code':'C1111','mpn':'FS8205A','package':'SOT-23-6'},
               {'code':'C2222','mpn':'FS8205A','package':'TSSOP-8'}]
        chosen,_=candidate_choice('Q1','FS8205A','SOP65P640X120-8N',parts,8)
        self.assertEqual(chosen['code'],'C2222')

    def test_sub_picofarad_value_is_preserved_in_search(self):
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.datasheets.fetch',return_value=b'{"code":200,"result":{}}') as request:
            LCSCClient(td).passive_search('C1','0.5pF','Capacitor_SMD:C_0402_1005Metric')
        self.assertEqual(request.call_args.kwargs['payload']['keyword'],'0.5pF 0402')

    def test_pdf_failure_does_not_discard_search_identity(self):
        p=fixture();p.datasheets={};p.components={'R1':p.components['R1']}
        candidate={'code':'C25744','mpn':'EXACT-PART','package':'0402','parameters':{'Resistance':'10kΩ'}}
        with tempfile.TemporaryDirectory() as td,patch.object(LCSCClient,'passive_search',return_value=[candidate]),patch.object(LCSCClient,'document',side_effect=DatasheetError('Unreadable PDF')):
            p.datasheets=collect_datasheets(p,td)
        self.assertEqual(p.datasheets['parts'][0]['code'],'C25744')
        self.assertEqual(p.datasheets['parts'][0]['pdf_status'],'failed')
        self.assertEqual(p.datasheets['parts'][0]['status'],'provisional')

    def test_footprint_hints_use_part_names_not_generic_dimensions(self):
        self.assertEqual(footprint_part_hint('TS-1187A-B-A-B:SW_TS-1187A-B-A-B'),'TS-1187A-B-A-B')
        self.assertEqual(footprint_part_hint('Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical'),'B2B-PH-K')
        self.assertEqual(footprint_part_hint('Capacitor_SMD:C_0402_1005Metric'),'')

    def test_group_selection_regenerates_bom_and_reuses_placement_snapshot(self):
        p=fixture()
        p.design_context=edit_context(p,empty_context(),{'part_choice':{'reference':'R1','references':['R1','R2'],'code':'C1111'}})
        doc={'code':'C1111','mpn':'SELECTED','package':'0402','manufacturer':'Example',
             'product_url':'https://www.lcsc.com/product-detail/C1111.html','pdf_url':'https://datasheet.lcsc.com/one.pdf','pages':[]}
        with tempfile.TemporaryDirectory() as td,patch.object(LCSCClient,'document',return_value=doc),patch.object(LCSCClient,'passive_search',return_value=[]):
            p.datasheets=collect_datasheets(p,td)
        with patch('pcb_checker.assembly_export.placement_snapshot') as native:p.assembly_export=generate_assembly(p,'source')
        native.assert_not_called()
        chosen=next(row for row in p.assembly_export['bom'] if row['code']=='C1111')
        self.assertEqual(chosen['references'],['R1','R2'])

    def test_declared_code_identity_survives_pdf_extraction_failure(self):
        p=fixture();p.components={'R1':p.components['R1']}
        p.design_context={'part_choices':{'R1':{'code':'C1111','signature':part_signature(p.components['R1'])}}}
        metadata={'code':'C1111','mpn':'SELECTED','package':'0402','manufacturer':'Example',
                  'product_url':'https://www.lcsc.com/product-detail/C1111.html','pdf_url':'https://datasheet.lcsc.com/one.pdf'}
        with tempfile.TemporaryDirectory() as td,patch.object(LCSCClient,'document',side_effect=DatasheetError('No text')):
            LCSCClient(td).write_cache('C1111-product.json',metadata)
            p.datasheets=collect_datasheets(p,td)
        self.assertEqual(p.datasheets['parts'][0]['code'],'C1111')
        self.assertEqual(p.datasheets['parts'][0]['pdf_url'],metadata['pdf_url'])
        self.assertEqual(p.datasheets['parts'][0]['status'],'ready')

    def test_web_analysis_sources_parts_by_default_and_saves_exports(self):
        with tempfile.TemporaryDirectory() as td:
            state=AppState(td,Path(td)/'reports');p=fixture()
            with patch('pcb_checker.webapp.analyze_project',return_value=p),patch('pcb_checker.webapp.collect_datasheets',return_value=p.datasheets) as lookup:
                state.start('analysis',{'path':td,'native':False});state.worker.join(5)
            lookup.assert_called_once();self.assertEqual(state.job['status'],'completed')
            self.assertTrue(state.report_path(state.report_id,'assembly-draft.zip').is_file());state.close()


if __name__=='__main__':unittest.main()
