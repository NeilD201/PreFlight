"""Product context reaches every AI path without a paid interview request."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.product_context import project_questions, product_evidence, validate_answers
from pcb_checker.ai.reviewer import evidence_packet
from pcb_checker.design_review import edit_context, empty_context, context_key, local_plan
from pcb_checker.step_help import _packet
from pcb_checker.repair_design import capture_repair_context
from pcb_checker.webapp import AppError
from test_design_review import project
import test_webapp as web_tests


class ProductContextTests(unittest.TestCase):
    def test_questions_use_parts_and_connections_not_product_name(self):
        from pcb_checker.models import Project
        p=project();p.archive='Wireless wearable with motors'
        result=project_questions(p,{'purpose':'Radio motor'})
        self.assertEqual(len([q for q in result['questions'] if q['id'].startswith('battery:')]),1)
        q=result['questions'][0]
        self.assertEqual(q['reference'],'U42')
        self.assertIn('R99 = 10k',q['known'])
        self.assertIn('battery',q['question'])
        self.assertEqual(project_questions(Project('wireless battery watch'))['questions'],[])
        p.components['U42'].schematic[0].dnp=True
        self.assertFalse(any(q['id'].startswith('battery:') for q in project_questions(p)['questions']))

    def test_answered_questions_do_not_hide_new_followups(self):
        from pcb_checker.check_scope import project_scope, YES
        p=project()
        first=project_questions(p)
        answers={q['id']:'Not sure yet' for q in first['questions']}
        external=next(q for q in project_scope(p)['questions'] if q['feature']=='external')
        answers[external['id']]=YES
        updated=project_questions(p,answers)
        self.assertFalse(answers.get(updated['questions'][0]['id']))
        self.assertTrue(any(q.get('feature')=='hotplug' for q in updated['questions']))
        evidence=product_evidence({'product_context':answers},p)
        self.assertTrue(any(r['id']=='hotplug' for r in evidence['check_scope']['special_reviews']))

    def test_changed_connections_reask_and_keep_old_answers_historical(self):
        p=project();key=project_questions(p)['questions'][0]['id']
        answers={key:'Not sure yet'}
        self.assertNotIn(key,project_questions(p,answers)['unanswered'])
        packet=product_evidence({'product_context':answers},p)
        self.assertIn(key,packet['unknown'])
        p.components['U42'].schematic_pins[1].net='OTHER'
        new=project_questions(p,answers)['questions'][0]['id']
        self.assertNotEqual(key,new)
        packet=product_evidence({'product_context':answers},p)
        self.assertIn(new,packet['unanswered'])
        self.assertEqual(packet['previous_revision_answers'],answers)
        self.assertNotIn(key,packet['answers'])

    def test_behavior_questions_ask_what_the_product_should_do(self):
        from pcb_checker.models import Project, Component, ComponentRecord, Pin
        def comp(ref, value, symbol, pins):
            return Component(ref,
                schematic=[ComponentRecord(ref, value, '', 'test.kicad_sch', 1, symbol=symbol)],
                schematic_pins=[Pin(n, name, net=net) for n, (name, net) in pins.items()])
        p = Project('behavior board')
        p.components = {
            'SW1': comp('SW1', 'RotaryEncoder_Switch', 'Device:RotaryEncoder_Switch',
                        {'A': ('A', 'Net-(SW1-A)'), 'B': ('B', 'Net-(SW1-B)'), 'C': ('C', 'GND'),
                         'S1': ('S1', 'Net-(SW1-S1)'), 'S2': ('S2', 'GND')}),
            'D1': comp('D1', 'LED', 'Device:LED', {'1': ('K', 'Net-(D1-K)'), '2': ('A', 'Net-(R1-2)')}),
            'R1': comp('R1', '10k', 'Device:R', {'1': ('1', 'Net-(D1-K)'), '2': ('2', '+3.3V')}),
            'U3': comp('U3', 'LD1117V', 'Regulator_Linear:LD1117V',
                       {'1': ('GND', 'GND'), '2': ('VO', '+3.3V'), '3': ('VI', '+5V')}),
        }
        p.design_context = {'product_context': {}}
        questions = project_questions(p)['questions']
        kinds = {q['id'].split(':')[0] for q in questions}
        self.assertIn('behavior', kinds)
        knob = next(q for q in questions if q['id'].startswith('behavior:SW1'))
        self.assertIn('turned or pressed', knob['question'])
        led = next(q for q in questions if q['id'].startswith('behavior:D1'))
        self.assertIn('indicator', led['question'])
        power = next(q for q in questions if q['id'].startswith('power:'))
        self.assertIn('receives power', power['question'])
        # No behavior question may demand facts already in the files.
        for q in questions:
            if q['id'].startswith('behavior:'):
                self.assertNotIn('netlist', q['question'].lower())

    def test_declared_battery_is_not_requested_again(self):
        from test_review import component
        from pcb_checker.models import Pin
        p=project();p.components['U42'].schematic_pins.append(Pin('3','VBAT',net='BAT'))
        p.components['BT1']=component('BT1','LP402030',[Pin('1',net='BAT')])
        q=project_questions(p)['questions'][0]
        self.assertIn('LP402030',q['known'])
        self.assertNotIn('Which battery',q['question'])
        self.assertIn('run while charging',q['question'])

    def test_validation_and_existing_context_preservation(self):
        p=project();current={**empty_context(),'description':'Keep description','operating_context':{'power':'3.3 V'}}
        updated=edit_context(p,current,{'product_context':{'purpose':'Logger'}})
        self.assertEqual(updated['description'],'Keep description')
        self.assertEqual(updated['operating_context'],current['operating_context'])
        self.assertNotIn('product_context',current)
        for bad in (None,[],{'api_key':'x'},{'purpose':9},{'purpose':'a'*1201},{'purpose':'bad\0'}):
            with self.subTest(bad=type(bad)),self.assertRaises(ValueError):validate_answers(bad)

    def test_matrix_switches_ask_once_not_per_switch(self):
        # Regression: a keyboard matrix produced ~90 "What should happen when
        # SWn is pressed?" questions. Identical switches ask once as a group;
        # distinct-role buttons (RST1) still ask individually.
        from pcb_checker.models import Component, ComponentRecord, Pin, Project
        p = Project('kb')
        p.design_context = {}
        for i in range(1, 6):
            ref = 'SW%d' % i
            rec = ComponentRecord(ref, 'TACT_SWITCH', 'Keys:MX', 'k.kicad_sch', i)
            p.components[ref] = Component(ref, [rec], [],
                [Pin('1', 'A', net='ROW%d' % i), Pin('2', 'B', net='COL%d' % i)])
        rst = ComponentRecord('RST1', 'SW_PUSH', 'Keys:SW', 'k.kicad_sch', 9)
        p.components['RST1'] = Component('RST1', [rst], [],
            [Pin('1', '1', net='GND'), Pin('2', '2', net='~{RESET}')])
        qs = project_questions(p)['all_questions']
        switch_qs = [q for q in qs if q['reference'] in {'SW1', 'SW2', 'SW3', 'SW4', 'SW5'}]
        self.assertEqual(len(switch_qs), 1)
        self.assertIn('SW5', switch_qs[0]['question'])
        self.assertEqual([q for q in qs if q['reference'] == 'RST1'][0]['id'].split(':')[0], 'behavior')

    def test_every_emitted_question_id_is_answerable(self):
        # Regression: answering a behavior/radio question failed with
        # "Use the supported product questions" because the validator allowlist
        # was narrower than the kind:ref:digest IDs project_questions emits
        # (and the beginner radio rewrite uses a bare 'radio' id).
        ids = ['behavior:SW1:0123456789', 'behavior:D100:abcdef0123',
               'body:J2:0123456789', 'battery:BT1:abcdef0123',
               'radio:J3:0123456789', 'loads:M1:abcdef0123',
               'connector:J1:0123456789', 'mode:SW1:abcdef0123']
        answers = {i: 'User answer' for i in ids}
        self.assertEqual(validate_answers(answers), answers)
        self.assertEqual(validate_answers({'radio': 'No wireless use'}),
                         {'radio': 'No wireless use'})

    def test_answers_are_in_review_chat_and_repair_evidence(self):
        p=project();answers={'purpose':'Battery logger','environment':'Outside in a metal case','operation':'May start while charging'}
        p.design_context={**empty_context(),'product_context':answers}
        self.assertEqual(evidence_packet(p)['design_context']['product_context']['answers'],answers)
        group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        chat=_packet(p,group,action,0,'What happens during startup?',[])
        self.assertEqual(chat['project_context']['product_context']['answers'],answers)
        repair=capture_repair_context({},p,group,{})
        self.assertEqual(repair['repair_context']['product_context']['answers'],answers)


class ProductPersistenceTests(unittest.TestCase):
    setUp=web_tests.StateTests.setUp
    tearDown=web_tests.StateTests.tearDown
    finish=web_tests.StateTests.finish

    def test_first_run_saves_answers_before_ai_and_reuses_them_on_update(self):
        self.state.key='fake-test-key'
        answers={'purpose':'Outdoor sensor','power':'Not sure yet','environment':'Rain and cold'}
        data={'path':str(self.source),'native':False,'datasheets':False,'ai':True,'product_context':answers}
        with patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'Offline test'}) as review:
            self.state.start('analysis',data)
            self.assertEqual(self.finish()['status'],'completed')
        p=review.call_args.args[0]
        self.assertEqual(p.design_context['product_context'],answers)
        self.assertEqual(self.state.store.design_context(context_key(p))['product_context'],answers)
        self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        self.assertEqual(self.state.project.design_context['product_context'],answers)
        other=self.source/'another';other.mkdir()
        for file in self.source.glob('*.kicad_*'):(other/file.name).write_bytes(file.read_bytes())
        self.state.start('analysis',{'path':str(other),'project_name':'Different product','native':False,'datasheets':False})
        self.assertEqual(self.finish()['status'],'completed')
        self.assertNotIn('product_context',self.state.project.design_context)

    def test_file_intake_pauses_ai_then_resumes_with_saved_answers(self):
        self.state.key='fake-test-key'
        parsed=project()
        with patch('pcb_checker.webapp.analyze_project',return_value=parsed), patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'Offline test'}) as review:
            # Presentation-only questions (experience level) never pause the
            # Vector review; the run proceeds with plain-language defaults.
            self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False,'ai':True,'context_intake':True})
            job=self.finish()
            self.assertEqual(job['status'],'completed')
            self.assertFalse(job['needs_product_context'])
            review.assert_called_once()
            # Hardware-driven questions still pause until answered.
            self.state.start('rethink',{'report_id':self.state.report_id,'context_revision':self.state.project.design_context['revision'],
                'product_context':{'experience':'Experienced EE — show electrical limits'},'datasheets':False,'ai':True,'context_intake':True})
            self.assertTrue(self.finish()['needs_product_context'])
            self.assertEqual(self.state.project.ai_review['status'],'needs_context')
            self.assertEqual(review.call_count,1)
            answers={q['id']:'Not sure yet' for q in project_questions(self.state.project)['questions']}
            self.state.start('rethink',{'report_id':self.state.report_id,'context_revision':self.state.project.design_context['revision'],
                'product_context':answers,'datasheets':False,'ai':True})
            self.assertEqual(self.finish()['status'],'completed')
            self.assertEqual(review.call_count,2)

    def test_missing_review_inputs_pause_updates_until_answers_are_saved(self):
        from pcb_checker.user_profile import guided_questions
        self.state.key='fake-test-key'
        with patch('pcb_checker.webapp.analyze_project',return_value=project()), \
             patch('pcb_checker.user_profile.guided_questions',side_effect=lambda *args,**kwargs:{**guided_questions(*args,**kwargs),'unanswered':[]}), \
             patch('pcb_checker.review_inputs.review_inputs',return_value={'tasks':[{'id':'missing-limit'}]}) as inputs, \
             patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'Offline test'}) as review:
            self.state.start('analysis',{'path':str(self.source),'native':False,'datasheets':False,'ai':True,'context_intake':True})
            self.assertTrue(self.finish()['needs_product_context'])
            self.state.start('rethink',{'report_id':self.state.report_id,'context_revision':self.state.project.design_context['revision'],
                'datasheets':False,'ai':True,'context_intake':True})
            self.assertTrue(self.finish()['needs_product_context'])
            self.assertEqual(self.state.project.ai_review['status'],'needs_context')
            review.assert_not_called()
            inputs.return_value={'tasks':[]}
            self.state.start('rethink',{'report_id':self.state.report_id,'context_revision':self.state.project.design_context['revision'],
                'datasheets':False,'ai':True,'context_intake':True})
            finished=self.finish()
            self.assertEqual(finished['status'],'completed',finished)
            self.assertFalse(finished['needs_product_context'])
            review.assert_called_once()

    def test_bad_answers_rejected_before_starting_worker(self):
        with self.assertRaises(AppError):
            self.state.start('analysis',{'path':str(self.source),'product_context':{'purpose':False}})
        self.assertIsNone(self.state.worker)


if __name__=='__main__':unittest.main()
