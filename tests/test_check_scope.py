import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.check_scope import build_scope, project_scope, YES, NO
from pcb_checker.review_sections import organize_review
from pcb_checker.design_review import context_key, empty_context
from pcb_checker.webapp import AppError
import test_webapp as web_tests


def board(*values):
    return {'components':{f'U{i}':{'schematic':[{'value':v,'symbol':v}],'schematic_pins':[]}
                          for i,v in enumerate(values)},'design_context':{'description':'Wired keyboard'}}


def answer(data,kind,value):
    question=next(q for q in build_scope(data)['questions'] if q['feature']==kind)
    data['design_context'].setdefault('product_context',{})[question['id']]=value


def rows(data):return {r['id']:r for r in build_scope(data)['checks']}


class ScopeTests(unittest.TestCase):
    def test_special_questions_follow_confirmed_use_and_update_review_scope(self):
        data=board('nRF52840','USB_C_Receptacle','DRV2605')
        features=lambda:{q['feature'] for q in build_scope(data)['intake_questions']}
        self.assertNotIn('radio_case',features())
        self.assertNotIn('hotplug',features())
        answer(data,'radio',YES);answer(data,'external',YES);answer(data,'pulse',YES)
        self.assertTrue({'radio_case','hotplug','long_cable','simultaneous'}<=features())
        answer(data,'wet',YES)
        special={r['id']:r for r in build_scope(data)['special_reviews']}
        self.assertEqual(special['wet']['selection'],'relevant')
        self.assertEqual(special['wet']['result'],'not_verified')
        answer(data,'radio',NO);answer(data,'external',NO)
        self.assertNotIn('radio_case',features())
        self.assertNotIn('hotplug',features())

    def test_scope_labels_are_english(self):
        scope=build_scope(board('ESP32'))
        titles=' '.join(r['title'] for r in scope['checks'])
        for text in ('EMV','Übersprechen','Erwärmung'):
            self.assertNotIn(text,titles)

    def test_wired_keyboard_does_not_invent_antenna_work_or_drop_emc(self):
        data=board('ATmega32U4','USB_C_Receptacle')
        result=rows(data)
        self.assertEqual(result['antenna']['selection'],'not_applicable')
        self.assertEqual(result['emc']['selection'],'relevant')
        self.assertEqual(result['esd']['selection'],'needs_context')
        answer(data,'external',YES)
        self.assertEqual(rows(data)['esd']['selection'],'relevant')
        self.assertTrue(all(r['result']!='checked' for r in rows(data).values()))

    def test_radio_hardware_requires_use_context_even_on_keyboard(self):
        data=board('nRF52840')
        self.assertEqual(rows(data)['antenna']['selection'],'needs_context')
        answer(data,'radio',NO)
        self.assertEqual(rows(data)['antenna']['selection'],'not_applicable')
        answer(data,'radio',YES)
        self.assertEqual(rows(data)['antenna']['selection'],'relevant')

    def test_product_use_survives_component_and_description_edits(self):
        data=board('nRF52840');answer(data,'radio',NO)
        changed=copy.deepcopy(data);changed['components']['U0']['schematic'][0]['value']='ESP32'
        self.assertEqual(rows(changed)['antenna']['selection'],'not_applicable')
        data['design_context']['description']='Wireless keyboard'
        self.assertEqual(rows(data)['antenna']['selection'],'not_applicable')
        answer(data,'radio',YES)
        self.assertEqual(rows(data)['antenna']['selection'],'relevant')

    def test_unrecognized_external_radio_can_be_included(self):
        data=board('CustomPart');answer(data,'radio',YES)
        self.assertEqual(rows(data)['antenna']['selection'],'relevant')
        data=board('CustomPart');data['design_context']['description']='Bluetooth device'
        self.assertEqual(rows(data)['antenna']['selection'],'needs_context')

    def test_dnp_radio_is_not_evidence_and_unknown_is_not_pass(self):
        data=board('nRF52840');data['components']['U0']['schematic'][0]['dnp']=True
        self.assertEqual(rows(data)['antenna']['evidence'],[])
        self.assertEqual(rows({})['thermal']['selection'],'needs_context')

    def test_unknown_antenna_stays_visible_until_radio_is_explicitly_unused(self):
        data=board('nRF52840');data['issues']=[{'code':'RF_FEED_NO_TERMINAL'}]
        data['review']={'groups':[{'id':'R1','issue_ids':['F1'],'signal_path_id':'rf-U0','category':'signal','priority':'review'}]}
        self.assertTrue(organize_review(data)['assignments']['R1']['attention'])
        answer(data,'radio',YES)
        self.assertTrue(organize_review(data)['assignments']['R1']['attention'])
        answer(data,'radio',NO)
        self.assertFalse(organize_review(data)['assignments']['R1']['attention'])
        data['issues'].append({'code':'KICAD_DRC_SHORTING_ITEMS'})
        data['review']['groups'][0]['issue_ids'].append('F2')
        self.assertTrue(organize_review(data)['assignments']['R1']['attention'])

    def test_ai_no_change_or_reference_label_cannot_hide_unresolved_rf(self):
        data=board('nRF52840');data['issues']=[{'code':'RF_FEED_NO_TERMINAL'}]
        data['review']={'groups':[{'id':'R1','issue_ids':['F1'],'signal_path_id':'rf-U0','category':'signal','priority':'note'}]}
        data['action_plan']={'actions':[{'group_id':'R1','disposition':'no_change','reference_only':True}]}
        row=organize_review(data)['assignments']['R1']
        self.assertTrue(row['attention']);self.assertEqual(row['section'],'traces')


class ScopePersistenceTests(unittest.TestCase):
    setUp=web_tests.StateTests.setUp
    tearDown=web_tests.StateTests.tearDown
    finish=web_tests.StateTests.finish

    def test_save_is_local_revision_checked_and_preserves_other_context(self):
        self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False,'ai':False})
        self.assertEqual(self.finish()['status'],'completed')
        project=self.state.project
        current=self.state.store.design_context(context_key(project)) or project.design_context or empty_context()
        scope=project_scope(project,current)
        request={'report_id':self.state.report_id,'context_revision':current.get('revision',0),
                 'signature':scope['signature'],'answers':{q['id']:NO for q in scope['questions']}}
        with patch('pcb_checker.webapp.run_ai_review') as ai:
            saved=self.state.save_check_scope(request)
            ai.assert_not_called()
        self.assertGreater(saved['revision'],current.get('revision',0))
        self.assertEqual(self.state.project_data()['check_scope']['context_revision'],saved['revision'])
        with self.assertRaises(AppError):self.state.save_check_scope(request)
        with self.assertRaises(AppError):self.state.save_check_scope({**request,'signature':'stale'})
        with self.assertRaises(AppError):self.state.save_check_scope({**request,'answers':{'bad':'yes'}})


if __name__=='__main__':unittest.main()
