import copy
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.project_search import search_project
from pcb_checker.project_chat import chat_packet
from test_design_review import project, with_doc

class ProjectSearchTests(unittest.TestCase):
    def test_natural_language_finds_datasheet_without_reference(self):
        p=with_doc(project())
        result=search_project(p,'Where does the exposed pad connect?')
        docs=[r for r in result['results'] if r['kind']=='datasheet']
        self.assertTrue(docs)
        self.assertIn('must be connected to VSS',docs[0]['evidence']['text'])
        self.assertEqual(docs[0]['evidence']['page'],1)

    def test_explicit_new_issue_beats_previous_conversation(self):
        p=project();other=copy.deepcopy(p.review['groups'][0]);other.update(id='R999',title='Separate radio issue',components=['U99'])
        p.review['groups'].append(other)
        packet=chat_packet(p,'Explain R999',[{'role':'user','text':'Explain R001'}])
        self.assertEqual(packet['related_evidence']['finding']['id'],'R999')
        self.assertEqual(packet['project_search']['results'][0]['id'],'R999')

    def test_net_search_and_results_bound(self):
        p=project();p.schematic_nets={'GND':['U42:6','R99:2']}
        result=search_project(p,'Which parts connect to ground?')
        net=next(r for r in result['results'] if r['kind']=='net')
        self.assertEqual(net['evidence']['pins'],['U42:6','R99:2'])
        self.assertLessEqual(len(result['results']),12)

    def test_unrelated_question_has_no_fake_search_match(self):
        self.assertEqual(search_project(project(),'Give me a dinner idea')['results'],[])

if __name__=='__main__':unittest.main()
