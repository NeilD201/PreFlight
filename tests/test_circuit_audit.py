import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import circuit_audit as audit
from pcb_checker.ai.reviewer import AIError, evidence_packet
from pcb_checker.datasheets import excerpts
from pcb_checker.models import Project, Component, ComponentRecord, Pin


def fixture():
    p = Project(archive='generic-board')
    for ref, value, pins in [('U1', 'SENSOR', [('1', 'VDD', 'VCC'), ('2', 'SDA', 'BUS'), ('3', 'ADDR', 'GND')]),
                             ('U2', 'MCU', [('1', 'IO', 'BUS'), ('2', 'VDD', 'VCC')]),
                             ('R1', '4.7k', [('1', '~', 'BUS'), ('2', '~', 'VCC')])]:
        p.components[ref] = Component(ref, schematic=[ComponentRecord(ref, value, '', 'test.kicad_sch', 1)],
                                     schematic_pins=[Pin(n, name, 'passive', net) for n, name, net in pins])
    text = 'Pin descriptions. SDA is an open-drain data signal and requires a pull-up resistor. ADDR may connect to GND to select address 0x48. Supply voltage must be between 1.7 V and 3.6 V.'
    p.datasheets = {'parts': [{'reference':'U1', 'code':'C123', 'status':'ready'}],
        'documents': {'C123': {'mpn':'SENSOR', 'sha256':'a'*64, 'pdf_url':'https://datasheet.lcsc.com/test.pdf', 'pages':[{'page':1,'text':text}]}}}
    return p


def response(scope, concern=False):
    doc = scope['datasheet_excerpts'][0]
    checks = []
    for area in audit.AREAS:
        checks.append({'area':area, 'status':'unresolved', 'title':area,
            'explanation':'The required operating detail is not supplied.',
            'comparison':{'requirement':'A bus operating limit is needed.','observed_design':'U1 SDA is on BUS.',
                          'reasoning':'The bus speed is not supplied.','consequence':'Timing performance is unresolved.'},
            'observations':[{'reference':'U1','pin':'2','net':'BUS'}], 'citations':[],
            'missing_context':['The selected bus clock rate is not supplied.'], 'steps':[]})
    checks[1].update(status='concern' if concern else 'consistent',
        explanation='The SDA connection has a pull-up through R1 to VCC.',
        citations=[{'evidence_id':doc['evidence_id'], 'quote':'SDA is an open-drain data signal and requires a pull-up resistor.'}],
        missing_context=[], steps=['Move U1 pin 2 to BUS.', 'Save and update the review.'] if concern else [])
    return {'parts':[{'reference':'U1','role':'Sensor on the MCU bus.', 'checks':checks}]}


class FunctionalAuditTests(unittest.TestCase):
    def setUp(self):
        self.p = fixture()
        self.packet = evidence_packet(self.p)
        self.docs = audit.functional_documents(self.p, ['U1'])
        self.scope = audit.scoped_packet(self.packet, ['U1'], self.docs)

    def test_target_parts_do_not_depend_on_erc_warnings(self):
        self.assertEqual(self.packet['action_group_ids'], [])
        self.assertEqual(audit.targets(self.packet), ['U1','U2'])
        self.p.components['U2'].schematic[0].dnp = True
        self.assertEqual(audit.targets(evidence_packet(self.p)), ['U1'])

    def test_scope_preserves_neighbor_other_pins_and_pullup(self):
        parts = {c['reference']:c for c in self.scope['components']}
        self.assertEqual(len(parts['U2']['pins']), 2)
        self.assertEqual(parts['R1']['value'], '4.7k')
        self.assertEqual(len(parts['R1']['pins']), 2)

    def test_detailed_review_receives_physical_process_and_neighbor_requirements(self):
        packet=copy.deepcopy(self.packet)
        packet['board_measurements']={'stackup':{'status':'saved'},'nets':[
            {'net':'BUS','min_width_mm':.15},{'net':'unrelated','min_width_mm':.1}]}
        packet['manufacturer_check']={'checks':[{'label':'Via in pad','advice':'Fill and cap'}]}
        packet['bus_candidates']=[{'net':'BUS','parallel_pullups':[{'ohms':2350}]}]
        packet['bypass_capacitors']=[{'reference':'R1','value':'test candidate'}]
        docs=self.docs+[dict(self.docs[0],evidence_id='D:neighbor',references=['U2'],
                             text='Supply voltage. The IO domain must be compatible with the bus pull-up supply.')]
        scope=audit.scoped_packet(packet,['U1'],docs)
        self.assertEqual(len(scope['board_measurements']['nets']),1)
        self.assertEqual(scope['manufacturer_check'],packet['manufacturer_check'])
        self.assertEqual(scope['bus_candidates'][0]['parallel_pullups'][0]['ohms'],2350)
        self.assertTrue(any(d['references']==['U2'] for d in scope['datasheet_excerpts']))
        self.assertIn('U2',scope['neighbor_requirement_coverage']['included'])

    def test_empty_comparison_is_not_accepted_as_deep_review(self):
        value=response(self.scope)
        value['parts'][0]['checks'][0]['comparison']['reasoning']=' '
        with self.assertRaises(AIError):audit.validate_parts(value,self.scope)

    def test_operating_guarantees_do_not_become_passed_checks(self):
        c=response(self.scope)['parts'][0]['checks'][1]
        c['comparison']['reasoning']='Connecting VIN to the battery provides sufficient headroom.'
        c['comparison']['consequence']='Ensures reliable communication.'
        bounded=audit.bound_comparison(c)
        self.assertEqual(bounded['status'],'unresolved')
        self.assertIn('not verified by net names',bounded['explanation'])
        self.assertEqual(bounded['steps'],[])
        self.assertNotIn('Ensures',str(bounded))
        c['comparison']['reasoning']='The saved pull-up connection matches the quoted requirement.'
        c['comparison']['consequence']='This does not establish reliable communication.'
        self.assertEqual(audit.bound_comparison(c)['status'],'consistent')

    def test_missing_pin_quote_wrong_component_and_omitted_area_rejected(self):
        for edit in ('pin','quote','owner','area'):
            value = response(self.scope)
            scope = copy.deepcopy(self.scope)
            if edit=='pin': value['parts'][0]['checks'][1]['observations'][0]['pin']='99'
            if edit=='quote': value['parts'][0]['checks'][1]['citations'][0]['quote']='SDA must connect directly to VCC.'
            if edit=='owner': scope['datasheet_excerpts'][0]['references']=['U2']
            if edit=='area': value['parts'][0]['checks'].pop()
            with self.subTest(edit=edit), self.assertRaises(AIError):audit.validate_parts(value, scope)

    def test_supported_connection_is_recorded_without_issue(self):
        with patch.object(audit,'_ask',return_value=(response(self.scope),{})) as request:
            result = audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(request.call_count,2)
        self.assertEqual(result['findings'],[])
        self.assertEqual(result['reviewed_count'],1)
        self.assertEqual(result['check_count'],5)
        self.assertEqual(result['unreviewed'][0]['reference'],'U2')
        self.assertEqual(result['status'],'partial')

    def test_second_review_can_remove_false_concern(self):
        with patch.object(audit,'_ask',side_effect=[(response(self.scope,True),{}),(response(self.scope),{})]) as request:
            result = audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(request.call_count,2)
        self.assertEqual(result['findings'],[])
        self.assertIn('audit',request.call_args.kwargs)

    def test_second_review_is_required_before_publishing_edit(self):
        with patch.object(audit,'_ask',side_effect=[(response(self.scope,True),{}),AIError('timed out')]):
            result = audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(result['findings'],[])
        self.assertEqual(result['status'],'partial')
        self.assertTrue(any('timed out' in x['message'] for x in result['unreviewed']))

    def test_concern_retains_exact_pins_and_sources(self):
        value = response(self.scope,True)
        with patch.object(audit,'_ask',return_value=(value,{})):
            result = audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        finding=result['findings'][0]
        self.assertEqual(finding['observations'],[{'reference':'U1','pin':'2','net':'BUS'}])
        self.assertEqual(finding['citations'][0]['evidence_id'],self.docs[0]['evidence_id'])

    def test_missing_datasheet_does_not_trigger_call_or_claim_review(self):
        self.p.datasheets={}
        with patch.object(audit,'_ask') as request:
            result=audit.run_circuit_audit(self.p,evidence_packet(self.p),model='test',key='test',provider='gemini')
        request.assert_not_called()
        self.assertEqual(result['reviewed_count'],0)
        self.assertEqual(len(result['unreviewed']),2)

    def test_expired_budget_does_not_start_request(self):
        with patch.object(audit,'AUDIT_SECONDS',0),patch.object(audit,'_ask') as request:
            result=audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        request.assert_not_called()
        self.assertEqual(result['findings'],[])

    def test_cancellation_is_not_swallowed(self):
        class Cancelled(Exception):pass
        def cancel(message):raise Cancelled()
        with self.assertRaises(Cancelled):audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini',progress=cancel)

    def test_citation_sources_survive_in_report_but_do_not_feed_next_request(self):
        doc={**self.docs[0],'evidence_id':'D:C123:p1:999','text':'Extra reviewed requirement.'}
        self.p.ai_review={'circuit_audit':{'documents':[doc]}}
        self.assertIn(doc,excerpts(self.p))
        self.assertNotIn(doc,excerpts(self.p,include_audit=False))
        self.p.datasheets['documents']['C123']['sha256']='b'*64
        self.assertNotIn(doc,excerpts(self.p))

    def test_functional_retrieval_covers_supply_and_interface_not_only_erc(self):
        pages=[{'page':n+1,'text':('Register details. '*200 if n<8 else 'Recommended operating conditions. Supply voltage must be 1.8 V.')} for n in range(9)]
        pages += [{'page':10,'text':'Pin descriptions. ADDR connects to ground for address 0x48.'}]
        self.p.datasheets['documents']['C123']['pages']=pages
        selected=audit.functional_documents(self.p,['U1'])
        self.assertTrue(any(d['page']==9 for d in selected))
        self.assertTrue(any(d['page']==10 for d in selected))
        self.assertLessEqual(len(selected), len(audit.TOPICS))

    def test_high_reasoning_and_no_persistent_provider_session(self):
        self.scope['design_context'] = {'description':'Product operating context. ' * 15000 + 'END_OF_CONTEXT'}
        value=response(self.scope)
        for c in value['parts'][0]['checks']:
            c['observation_ids']=['P:U1:2'];c.pop('observations')
            c['quotation_ids']=[];c.pop('citations')
        answer={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(value)}]}]}
        with patch.object(audit,'post_ai',return_value=answer) as post:
            audit._ask(self.scope,model='test',key='private',provider='gemini',timeout=45)
        payload=post.call_args.args[0]
        sent=json.loads(payload['input'])
        self.assertGreater(len(payload['input'].encode()),300_000)
        self.assertEqual(sent['design_context'],self.scope['design_context'])
        self.assertIn('pcb_positions',sent['components'][0])
        self.assertIn('pads',sent['components'][0])
        self.assertIn('manufacturer_check',sent)
        self.assertIn('board_measurements',sent)
        self.assertIn('comparison',payload['response_format']['schema']['properties']['parts']['items']['properties']['checks']['items']['properties'])
        self.assertEqual(payload['generation_config']['thinking_level'],'high')
        self.assertFalse(payload['store'])
        self.assertEqual(post.call_args.kwargs,{'max_attempts':3,'timeout':45})

    def test_contiguous_quote_options_remain_exact_and_bounded(self):
        text=('A long wrapped requirement about supply bypass capacitors.\n'*40)
        quotes=audit.quotation_options([{'evidence_id':'D:test','text':text}])
        self.assertTrue(quotes)
        for quote in quotes:
            self.assertIn(quote['quote'],text)
            self.assertLessEqual(len(quote['quote']),350)

    def test_bad_part_does_not_discard_other_part(self):
        value=response(self.scope)
        other=copy.deepcopy(value['parts'][0]);other['reference']='U2'
        accepted, failures=audit.accepted_parts({'parts':value['parts']+[other]}, {**self.scope,'target_references':['U1','U2']})
        self.assertEqual([p['reference'] for p in accepted],['U1'])
        self.assertEqual([p['reference'] for p in failures],['U2'])

    def test_explicit_short_quota_reset_is_honored_once(self):
        rate=AIError('rate limit',diagnostics={'http_status':429,'service_message':'Please retry in 1s.'})
        with patch.object(audit,'_ask',side_effect=[rate,(response(self.scope),{}),(response(self.scope),{})]) as request,patch.object(audit.time,'sleep') as sleep:
            result=audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(request.call_count,3)
        self.assertEqual(sleep.call_count,2)
        self.assertEqual(result['reviewed_count'],1)

    def test_exhausted_quota_does_not_retry(self):
        rate=AIError('daily quota exhausted',diagnostics={'http_status':429})
        with patch.object(audit,'_ask',side_effect=rate) as request,patch.object(audit.time,'sleep') as sleep:
            result=audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(request.call_count,1);sleep.assert_not_called()
        self.assertEqual(result['reviewed_count'],0)

    def test_same_pin_concern_in_two_areas_produces_one_issue(self):
        value=response(self.scope,True)
        value['parts'][0]['checks'][4]={**copy.deepcopy(value['parts'][0]['checks'][1]),'area':'signal_path'}
        with patch.object(audit,'_ask',return_value=(value,{})):
            result=audit.run_circuit_audit(self.p,self.packet,model='test',key='test',provider='gemini')
        self.assertEqual(len(result['findings']),1)

if __name__=='__main__':unittest.main()
