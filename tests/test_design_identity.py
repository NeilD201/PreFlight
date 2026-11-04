"""Saved design identity is separate from supplier selection and physical assembly."""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.design_identity import design_identity, INSTRUCTIONS
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.project_chat import chat_packet
from pcb_checker.step_help import _packet
from test_design_review import project, with_doc


def sample():
    p=with_doc(project());c=p.components['U42']
    for r in c.schematic+c.pcb:
        r.value='ABC123';r.footprint='Custom:LGA14L';r.properties={'MP':'ABC123'}
    p.datasheets['parts'][0]['status']='provisional'
    d=p.datasheets['documents']['C150772'];d.update(mpn='ABC123TR',package='LGA-14')
    d['pages']=[{'page':1,'text':'Product summary\nOrder code ABC123 ABC123TR\nPackage LGA-14\nPacking Tray Tape and reel'}]
    return p


class IdentityTests(unittest.TestCase):
    def test_ordering_table_establishes_design_without_approving_purchasing(self):
        p=sample();before=copy.deepcopy(p.datasheets)
        identity=design_identity(p,'U42')
        self.assertEqual(identity['status'],'documented')
        self.assertEqual(identity['declared_mpn'],'ABC123')
        self.assertEqual(identity['package_family_check'],'consistent')
        self.assertEqual(identity['purchasing_status'],'provisional')
        self.assertEqual(p.datasheets,before)
        self.assertIn(identity['document']['quote'],p.datasheets['documents']['C150772']['pages'][0]['text'])

    def test_no_suffix_stripping_or_substring_family_matching(self):
        p=sample();d=p.datasheets['documents']['C150772']
        d['pages'][0]['text']='ABC123 is mentioned elsewhere.\nOrder code ABC123X ABC123TR'
        self.assertEqual(design_identity(p,'U42')['status'],'unresolved')
        d['pages'][0]['text']='ABC123 ABC123TR are discussed but no ordering section is available.'
        self.assertEqual(design_identity(p,'U42')['status'],'unresolved')

    def test_conflicting_design_or_bom_or_package_is_not_auto_accepted(self):
        for field in ('pcb','bom','package','mpn'):
            p=sample();c=p.components['U42']
            if field=='pcb':c.pcb[0].value='ABC456'
            if field=='bom':c.bom=[{'mpn':'ABC456','valid':True}]
            if field=='package':p.datasheets['documents']['C150772']['package']='QFN-24'
            if field=='mpn':c.schematic[0].properties['MP']='ABC456'
            with self.subTest(field=field):self.assertEqual(design_identity(p,'U42')['status'],'conflict')

    def test_review_and_both_chats_receive_the_same_check(self):
        p=sample();g=p.review['groups'][0]
        packets=[evidence_packet(p),chat_packet(p,'What is U42?',[]),
                 _packet(p,g,{'steps':['Explain U42.']},0,'Explain U42',[])]
        for packet in packets:
            c=next(c for c in packet['components'] if c['reference']=='U42')
            self.assertEqual(c['design_identity']['status'],'documented')
        from pcb_checker.ai.pin_review import INSTRUCTIONS as pins
        from pcb_checker.project_chat import INSTRUCTIONS as chat
        from pcb_checker.web_chat import WEB_INSTRUCTIONS
        for prompt in (pins,chat,WEB_INSTRUCTIONS):self.assertIn(INSTRUCTIONS,prompt)

if __name__=='__main__':unittest.main()
