"""Real PDF passages, bounded assets, and source-matched schematic previews."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'.deps'))
from pcb_checker.issue_proof import render_crop, official_document, issue_proof
from pcb_checker.schematic_visual import render_schematic, schematic_locations
from pcb_checker.parsers.sexpr import parse
from test_review import fixture
from pcb_checker.review import build_review


def tiny_pdf(path,text='The exposed pad must connect to ground.'):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject,DictionaryObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=400,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject();stream.set_data(('BT /F1 12 Tf 30 240 Td ('+text+') Tj ET').encode('ascii'))
    page[NameObject('/Contents')]=writer._add_object(stream)
    writer.write(path)


class EvidenceTests(unittest.TestCase):
    def test_uuid_pin_locations_use_sheet_coordinates_and_symbol_transforms(self):
        source='''(kicad_sch (lib_symbols (symbol "Test:Chip"
          (symbol "Chip_1_1" (pin input line (at 15.24 2.54 180) (name "GPIO1") (number "7")))))
          (symbol (lib_id "Test:Chip") (at -30.48 265.43 0) (unit 1) (uuid "placed")
            (property "Reference" "STALE") (pin "7" (uuid "pin-uuid")))
          (symbol (lib_id "Test:Chip") (at 100 50 90) (mirror x) (unit 1) (uuid "rotated")
            (property "Reference" "U4") (pin "7" (uuid "rotated-pin")))
          (global_label "BAT" (at 55.88 -2.54 0) (uuid "label-uuid")))'''
        loc,items,pins=schematic_locations(parse(source,'kicad_sch'),{'placed':'U3'})
        self.assertEqual(loc['U3'],[[-30.48,265.43]])
        self.assertEqual(items['pin-uuid']['point'],[-15.24,262.89])
        self.assertEqual(items['pin-uuid']['reference'],'U3')
        self.assertEqual(items['rotated-pin']['point'],[102.54,34.76])
        self.assertEqual(items['label-uuid']['point'],[55.88,-2.54])
        self.assertEqual(pins['U3']['7']['name'],'GPIO1')

    def test_pin_of_another_symbol_unit_is_not_used(self):
        root=parse('''(kicad_sch (lib_symbols (symbol "Test:Chip"
          (symbol "Chip_1_1" (pin input line (at 10 0) (number "7")))
          (symbol "Chip_2_1" (pin input line (at 99 0) (number "7")))))
          (symbol (lib_id "Test:Chip") (at 20 30) (unit 1) (uuid "placed")
            (property "Reference" "U3") (pin "7" (uuid "pin"))))''','kicad_sch')
        self.assertEqual(schematic_locations(root,{})[1]['pin']['point'],[30,30])

    def test_known_requirement_uses_matching_cached_pdf_without_network(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'.datasheets').mkdir();pdf=root/'source.pdf';tiny_pdf(pdf)
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest();pdf.replace(root/'.datasheets'/(digest+'.pdf'))
            p=fixture();p.review=build_review(p)
            p.datasheets={'parts':[{'reference':'U42','status':'ready','code':'C12345'}],
              'documents':{'C12345':{'mpn':'MCP73831-2ACI/MC','sha256':digest,'pdf_url':'https://datasheet.lcsc.com/test.pdf',
                'pages':[{'page':1,'text':'The exposed pad must connect to ground.'}]}}}
            g={**p.review['groups'][0],'rule':'test-known','components':['U42']}
            with patch.dict('pcb_checker.issue_proof.KNOWN',{'test-known':('microchip',1,'The exposed pad must connect to ground.')}),patch('pcb_checker.issue_proof.official_document') as download:
                result=issue_proof(p,g,root)
            download.assert_not_called();self.assertTrue(result['pictures'][0]['matched'])
            self.assertEqual(result['pictures'][0]['url'],'https://datasheet.lcsc.com/test.pdf#page=1')

    def test_pin_analysis_citations_generate_real_deduplicated_images(self):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'.datasheets').mkdir();pdf=root/'source.pdf';tiny_pdf(pdf)
            digest=hashlib.sha256(pdf.read_bytes()).hexdigest();pdf.replace(root/'.datasheets'/(digest+'.pdf'))
            p=fixture();p.review=build_review(p);g=p.review['groups'][0]
            p.datasheets={'parts':[{'reference':'U42','status':'ready','code':'C12345'}],
              'documents':{'C12345':{'mpn':'TEST-PART','sha256':digest,'pdf_url':'https://datasheet.lcsc.com/test.pdf',
                'pages':[{'page':1,'text':'The exposed pad must connect to ground.'}]}}}
            citation={'evidence_id':'D:C12345:p1:0','quote':'The exposed pad must connect to ground.'}
            p.action_plan={'actions':[{'group_id':g['id'],'citations':[],
                'pin_checks':[{'citations':[citation]},{'citations':[citation]}]}]}
            result=issue_proof(p,g,root)
            self.assertEqual(len(result['pictures']),1)
            self.assertTrue(result['pictures'][0]['matched'])
            self.assertEqual(result['pictures'][0]['title'],'TEST-PART')
    def test_real_text_is_cropped_but_missing_quote_is_not_highlighted(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pdf=root/'test.pdf';tiny_pdf(pdf)
            found=render_crop(pdf,1,'The exposed pad must connect to ground.',root)
            self.assertTrue(found['matched']);self.assertLess(found['height'],300)
            self.assertTrue((root/Path(found['image']).name).read_bytes().startswith(b'\x89PNG'))
            missing=render_crop(pdf,1,'Invented electrical requirement.',root)
            self.assertFalse(missing['matched']);self.assertGreater(missing['height'],found['height'])
            with self.assertRaises(ValueError):render_crop(pdf,9,'A missing page.',root)

    def test_arbitrary_urls_and_missing_source_are_not_fetched(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):official_document('http://127.0.0.1/private',Path(td))
            p=fixture();p.review=build_review(p);group={**p.review['groups'][0],'rule':'unknown','sources':[]}
            result=issue_proof(p,group,Path(td))
            self.assertFalse(result['pictures']);self.assertTrue(result['rule_findings'])

    def test_table_heading_keeps_table_page_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);pdf=root/'table.pdf';tiny_pdf(pdf,'Table 19. Internal pin status')
            result=render_crop(pdf,1,'Table 19. Internal pin status',root)
            self.assertTrue(result['matched'])
            self.assertEqual(result['height'],600)
            self.assertIn('table or figure',result['caption'])

    def test_schematic_changed_since_report_is_rejected(self):
        p=fixture();p.selected_files={'schematic':'test.kicad_sch'};p.provenance={'source_hashes':{'test.kicad_sch':'old'}}
        with patch('pcb_checker.schematic_visual.find_kicad',return_value='/kicad'),patch('pcb_checker.schematic_visual.read_project_file',return_value=b'changed'):
            meta,assets=render_schematic(p,'/project')
        self.assertEqual(meta['status'],'unavailable');self.assertIn('changed',meta['message']);self.assertFalse(assets)

    def test_active_content_in_schematic_svg_is_rejected(self):
        import hashlib
        p=fixture();p.selected_files={'schematic':'test.kicad_sch'}
        content=b'(kicad_sch (version 20250114) (uuid "root"))'
        p.provenance={'source_hashes':{'test.kicad_sch':hashlib.sha256(content).hexdigest()}}
        def export(binary,args,folder):
            (folder/'rendered'/'test.svg').write_text('<svg viewBox="0 0 100 100"><script>bad()</script></svg>')
        with patch('pcb_checker.schematic_visual.find_kicad',return_value='/kicad'),patch('pcb_checker.schematic_visual.read_project_file',return_value=content),patch('pcb_checker.schematic_visual._stage'),patch('pcb_checker.schematic_visual._run',side_effect=export):
            meta,assets=render_schematic(p,'/project')
        self.assertEqual(meta['status'],'unavailable');self.assertFalse(assets)
