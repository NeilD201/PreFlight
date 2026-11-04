import copy
from dataclasses import asdict
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import AIError, DESIGN_SCHEMA, allowed_evidence, evidence_packet, run_ai_review, validate_review
from pcb_checker.datasheets import (DatasheetError, LCSCRedirect, LCSCClient, collect_datasheets,
                                    excerpts, product_metadata, safe_url)
from pcb_checker.design_review import compose_plan, context_key, edit_context, empty_context, local_plan, refresh_context_from_store
from pcb_checker.step_help import answer_step
from pcb_checker.models import Pad
from pcb_checker.report import save_report, html_report
from pcb_checker.review import build_review
from pcb_checker.storage import RevisionConflict, WorkspaceStore
from pcb_checker.webapp import AppState, AppError
from pcb_checker.workflow import keyed_groups
from test_review import fixture


def project():
    p=fixture();p.review=build_review(p);p.design_context=empty_context()
    return p


DOC = {'code':'C150772','mpn':'MCP73831-2ACI/MC','manufacturer':'Microchip','package':'DFN-8',
       'product_url':'https://www.lcsc.com/product-detail/C150772.html',
       'pdf_url':'https://datasheet.lcsc.com/test.pdf','sha256':'a'*64,'page_count':1,'partial':False,
       'pages':[{'page':1,'text':'The exposed pad must be connected to VSS. Check the recommended layout.'}]}


def with_doc(p):
    p.datasheets={'parts':[{'reference':'U42','status':'ready','code':'C150772'}], 'documents':{'C150772':copy.deepcopy(DOC)}}
    return p


def design_result(packet):
    eid=packet['datasheet_excerpts'][0]['evidence_id'] if packet['datasheet_excerpts'] else None
    return {'summary':'Review the charger layout.','answer':'','device_description':'A board with a lithium-cell charger.',
            'device_evidence_ids':['C:U42'],'circuit_findings':[],'assumptions':['Battery capacity is unknown.'],
            'actions':[{'group_id':gid,'title':'Inspect the charger','disposition':'investigate',
                'reason':'Confirm the exposed pad assignment.','steps':['Inspect U42 pad 9.','Rerun the connection check.'],
                'context_response':'Your explanation needs supporting evidence.','evidence_ids':[gid,'C:U42'],
                'pin_checks':[], 'citations':[{'evidence_id':eid,'quote':'The exposed pad must be connected to VSS.'}] if eid else []}
                for gid in packet['action_group_ids']], 'questions':[],'limitations':['Text excerpts omit figures.']}


class DatasheetTests(unittest.TestCase):
    def test_download_hosts_reject_credentials_private_hosts_and_redirects(self):
        for url in ['http://www.lcsc.com/a.pdf','https://127.0.0.1/a.pdf','https://lcsc.com.evil.test/a.pdf',
                    'https://x@www.lcsc.com/a.pdf','https://datasheet.lcsc.com:123/a.pdf','file:///etc/passwd']:
            with self.subTest(url=url), self.assertRaises(DatasheetError):safe_url(url)
        self.assertEqual(safe_url(DOC['pdf_url']),DOC['pdf_url'])
        with self.assertRaises(DatasheetError):LCSCRedirect().redirect_request(None,None,302,'',{},'https://example.org/a.pdf')

    def test_metadata_is_bound_to_requested_sku_not_an_alternative(self):
        item={'@type':'Product','sku':'C150772','mpn':'MCP73831-2ACI/MC','brand':{'name':'Microchip'},
              'subjectOf':{'name':'Datasheet','url':DOC['pdf_url']},'additionalProperty':[{'name':'Package','value':'DFN-8'}]}
        other={**item,'sku':'C12345','mpn':'OTHER','subjectOf':{'url':'https://datasheet.lcsc.com/other.pdf'}}
        html=''.join('<script type="application/ld+json">'+json.dumps(x)+'</script>' for x in [other,item])
        self.assertEqual(product_metadata(html,'C150772')['mpn'],'MCP73831-2ACI/MC')
        self.assertEqual(product_metadata(html,'C150772')['pdf_url'],DOC['pdf_url'])
        with self.assertRaises(DatasheetError):product_metadata(html,'C99999')
        item['subjectOf']['url']='https://evil.test/stolen.pdf'
        with self.assertRaises(DatasheetError):product_metadata('<script type="application/ld+json">'+json.dumps(item)+'</script>','C150772')

    def test_ambiguous_family_is_fetched_but_labelled_provisional(self):
        p=project()
        from pcb_checker.models import Pad
        p.components['U42'].pcb[0].pads=[Pad(str(n),'',1) for n in range(1,10)]
        candidates=[{k:DOC[k] for k in ('code','mpn','package','manufacturer','product_url')}]
        with tempfile.TemporaryDirectory() as td, patch.object(LCSCClient,'search',return_value=candidates), patch.object(LCSCClient,'document',return_value=DOC) as get:
            p.datasheets=collect_datasheets(p,td)
        self.assertEqual(p.datasheets['parts'][0]['status'],'provisional')
        get.assert_called_once();self.assertEqual(excerpts(p)[0]['part_match'],'provisional')

    def test_explicit_selection_fetches_real_identity_and_excerpts(self):
        p=project();p.design_context=edit_context(p,empty_context(),{'part_choice':{'reference':'U42','code':'C150772'}})
        with tempfile.TemporaryDirectory() as td, patch.object(LCSCClient,'search') as search, patch.object(LCSCClient,'document',return_value=DOC):
            p.datasheets=collect_datasheets(p,td)
        search.assert_not_called();self.assertEqual(p.datasheets['ready'],1)
        self.assertEqual(excerpts(p)[0]['references'],['U42'])
        self.assertEqual(excerpts(p)[0]['page'],1)

    def test_changed_part_does_not_reuse_old_selection(self):
        p=project();p.design_context=edit_context(p,empty_context(),{'part_choice':{'reference':'U42','code':'C150772'}})
        p.components['U42'].schematic[0].value='Different IC'
        with tempfile.TemporaryDirectory() as td, patch.object(LCSCClient,'search',return_value=[]) as search, patch.object(LCSCClient,'document') as get:
            result=collect_datasheets(p,td)
        search.assert_called_once();get.assert_not_called();self.assertEqual(result['ready'],0)

    def test_declared_mpn_code_conflict_is_not_evidence(self):
        p=project();p.assembly={'parts':[{'reference':'U42','mpn':'OTHER','lcsc':'C150772'}]}
        with tempfile.TemporaryDirectory() as td, patch.object(LCSCClient,'document',return_value=DOC):p.datasheets=collect_datasheets(p,td)
        self.assertEqual(p.datasheets['parts'][0]['status'],'identity_conflict');self.assertEqual(excerpts(p),[])

    def test_cache_reuses_text_without_network(self):
        with tempfile.TemporaryDirectory() as td:
            client=LCSCClient(td);client.write_cache('C150772.json',DOC)
            with patch('pcb_checker.datasheets.fetch') as fetch:
                self.assertTrue(client.document('C150772')['cached'])
                fetch.assert_not_called()

    def test_search_filters_other_voltage_variants_and_unrelated_results(self):
        response={'code':200,'result':{'exactMatchResult':[
            {'productCode':'C11111','productModel':'AP2112K-1.8TRG1'},
            {'productCode':'C22222','productModel':'AP2112K-3.3TRG1'},
            {'productCode':'C33333','productModel':'LED123'}]}}
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.datasheets.fetch',return_value=json.dumps(response).encode()):
            candidates=LCSCClient(td).search('AP2112K-3.3')
        self.assertEqual([c['code'] for c in candidates],['C22222'])

    def test_generic_component_labels_are_not_searched(self):
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.datasheets.fetch') as fetch:
            self.assertEqual(LCSCClient(td).search('LED'),[])
            fetch.assert_not_called()


class DesignPlanTests(unittest.TestCase):
    def setUp(self):
        # Each test supplies a different simulated provider response. Do not
        # let a successful fixture review bypass the next validation scenario.
        from pcb_checker.ai.reviewer import _REVIEW_CACHE
        _REVIEW_CACHE.clear()

    def test_shared_operating_context_is_validated_and_sent_with_placement_evidence(self):
        p=project()
        p.design_context=edit_context(p,empty_context(),{'operating_context':{
            'interfaces':'U3 GPIO1 is an input.', 'power':'One Li-ion cell.', 'radio':'External antenna.'}})
        p.design_context=edit_context(p,p.design_context,{'description':'Wearable ECG.'})
        packet=evidence_packet(p)
        self.assertEqual(packet['design_context']['operating_context']['radio'],'External antenna.')
        self.assertEqual(packet['design_context']['operating_context']['interfaces'],'U3 GPIO1 is an input.')
        self.assertIn('pcb_positions',packet['components'][0])
        for invalid in ({'unexpected':'text'}, {'radio':'x'*1501}, {'radio':None}):
            with self.assertRaises(ValueError):edit_context(p,p.design_context,{'operating_context':invalid})

    def test_charger_steps_use_ground_name_and_plain_language(self):
        p=project()
        group={'id':'R001','title':'U42: exposed pad','rule':'mcp73831_dfn','why':'Technical explanation',
               'priority':'review','components':['U42'],'issue_ids':[],'evidence':[],'sources':[]}
        p.review={'groups':[group]}
        p.components['U42'].pcb[0].pads.append(Pad('6','GND',1))
        action=local_plan(p)['actions'][0]
        self.assertIn('bottom metal pad',action['steps'][0])
        self.assertIn('GND',action['steps'][0])
        self.assertIn('Open the PCB',action['steps'][1])
        self.assertNotIn('ordering code', ' '.join(action['steps']))
        self.assertIn('metal pad underneath',action['reason'])
        self.assertNotIn('All datasheets', ' '.join(action['steps']))
        guide=answer_step(p,group,action,0,'What is ground?',[],key='')
        self.assertEqual(guide['status'],'local')
        self.assertIn('Ground',guide['answer'])

    def test_step_help_sends_only_selected_finding(self):
        p=project();group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        reply={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':'{"answer":"Open U42 in KiCad and check the pad."}'}]}]}
        with patch('pcb_checker.step_help.post_ai',return_value=reply) as post:
            result=answer_step(p,group,action,0,'Where do I click?',[],key='secret',model='test-model')
        self.assertEqual(result['status'],'ai')
        payload=post.call_args.args[0]
        self.assertIn('Where do I click?',payload['input'])
        self.assertNotIn('secret',payload['input'])
        self.assertLess(len(payload['input']),28000)
        self.assertIn('project_context',payload['input'])

    def test_step_help_includes_external_part_named_in_fix(self):
        from pcb_checker.step_help import _packet
        p=project();group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        action['steps']=['Replace R99 with the correct charge-setting resistor.']
        group['chain_targets']=[{'reference':'R99','pin':'1'}]
        packet=_packet(p,group,action,0,'Which footprint should I use?',[])
        components={part['reference']:part for part in packet['components']}
        self.assertIn('U42',components)
        self.assertEqual(components['R99']['value'],'10k')
        self.assertEqual(components['R99']['footprint'],'test:package')
        self.assertEqual(packet['selected_step'],action['steps'][0])
        self.assertIn('board_stackup',packet['project_context'])
        self.assertIn('source_revision',packet['project_context'])

    def test_repair_assessment_prioritizes_selected_path_and_its_passives(self):
        from pcb_checker.step_help import _packet, _routing_context
        p=project();group=p.review['groups'][0];action=local_plan(p)['actions'][0]
        group['signal_path_id']='selected-path'
        p.signal_paths={'paths':[{'id':'selected-path','anchor':'U42','nets':[{'net':'PROG'}],
                                 'terminal':None,'limits':'Not an RF simulation'}]}
        p.design_context['operating_context']={'radio':'External antenna'}
        def chain(i,net):
            return {'id':str(i),'kind':'signal','title':net,'nets':[net],
                    'components':['U42','R99'],'endpoints':[
                        {'reference':'R99','pin':'1','net':net,'board_matches':True}]}
        p.circuit={'routing_chains':{'status':'ready','chains':[
            chain(1,'POWER1'),chain(2,'POWER2'),chain(3,'POWER3'),chain(4,'PROG')]}}
        routing=_routing_context(p,['U42'],{'PROG'})
        self.assertEqual(routing['chains'][0]['id'],'4')
        self.assertEqual(routing['chains'][0]['endpoints'][0]['reference'],'R99')
        packet=_packet(p,group,action,0,'Investigate a fix',[])
        self.assertIn('R99',{c['reference'] for c in packet['components']})
        self.assertEqual(packet['operating_context']['radio'],'External antenna')
        self.assertIsNone(packet['relevant_signal_paths'][0]['terminal'])
        self.assertEqual(packet['routing_chains']['chains'][0]['nets'],['PROG'])

    def test_intent_changes_plan_but_not_the_findings(self):
        p=project();before=asdict(p)['issues'];group=keyed_groups(asdict(p))[0]
        p.design_context=edit_context(p,empty_context(),{'intention':{'key':group['key'],'note':'Test point is intentionally unused.'}})
        plan=local_plan(p)
        self.assertEqual(plan['actions'][0]['disposition'],'intentional')
        self.assertIn('not been independently verified',plan['actions'][0]['context_response'])
        self.assertEqual(before,asdict(p)['issues'])
        p.review['groups'][0]['summary']='Changed evidence'
        self.assertNotEqual(local_plan(p)['actions'][0]['disposition'],'intentional')

    def test_context_is_in_ai_input_but_obsolete_notes_are_excluded(self):
        p=project();g=keyed_groups(asdict(p))[0]
        p.design_context=edit_context(p,empty_context(),{'description':'External battery pack.','intention':{'key':g['key'],'note':'Uses an external pullup.'}})
        packet=evidence_packet(p)
        self.assertEqual(packet['design_context']['description'],'External battery pack.')
        self.assertEqual(packet['design_context']['intentions'][0]['group_id'],g['id'])
        p.review['groups'][0]['summary']='Different pins'
        self.assertEqual(evidence_packet(p)['design_context']['intentions'],[])

    def test_pdf_quote_and_page_are_validated(self):
        packet=evidence_packet(with_doc(project()));value=design_result(packet)
        validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        for mutation in ('quote','page','duplicate','missing','invented_intent','wrong_part'):
            bad=copy.deepcopy(value)
            if mutation=='quote':bad['actions'][0]['citations'][0]['quote']='The exposed pad must be connected to VCC.'
            if mutation=='page':bad['actions'][0]['citations'][0]['evidence_id']='D:C150772:p99:0'
            if mutation=='duplicate':bad['actions'].append(bad['actions'][0])
            if mutation=='missing':bad['actions']=[]
            if mutation=='invented_intent':bad['actions'][0]['disposition']='intentional'
            if mutation=='wrong_part':bad['actions'][0]['evidence_ids']=['R999']
            with self.subTest(mutation=mutation),self.assertRaises(AIError):validate_review(bad,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_design_request_contains_context_pdf_and_strict_schema(self):
        p=with_doc(project());p.design_context['description']='Wearable prototype.'
        packet=evidence_packet(p);value=design_result(packet)
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(value)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response) as post:
            result=run_ai_review(p,key='test-secret',design=True)
        payload=post.call_args.args[0]
        self.assertEqual(payload['response_format']['schema'],DESIGN_SCHEMA)
        self.assertLess(len(json.dumps(payload['response_format']['schema'])),5000)
        self.assertIn('Wearable prototype.',payload['input']);self.assertIn('exposed pad',payload['input'])
        self.assertEqual(result['status'],'completed')
        p.ai_review=result;p.action_plan=compose_plan(p)
        self.assertEqual(p.action_plan['status'],'ai')
        self.assertIn('Inspect U42 pad 9.',html_report(p))

    def test_invalid_evidence_gets_one_validated_correction(self):
        p=with_doc(project());packet=evidence_packet(p);good=design_result(packet)
        bad=copy.deepcopy(good);bad['actions'][0]['evidence_ids']=['C:INVENTED']
        response=lambda value:{'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(value)}]}]}
        events=[]
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=[response(bad),response(good)]) as post:
            result=run_ai_review(p,key='test-secret',design=True,progress=events.append)
        self.assertEqual(result['status'],'completed');self.assertTrue(result['validation_repaired'])
        self.assertEqual(result['result']['summary'],f"Evidence checks accepted AI guidance for all {len(good['actions'])} issues.")
        self.assertEqual(post.call_count,2);self.assertEqual(post.call_args.kwargs['max_attempts'],3)
        self.assertIn('validation_error',post.call_args.args[0]['input']);self.assertTrue(events)

    def test_empty_investigation_steps_receive_specific_correction_feedback(self):
        p=with_doc(project());packet=evidence_packet(p);good=design_result(packet)
        bad=copy.deepcopy(good);bad['actions'][0]['steps']=[]
        response=lambda value:{'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(value)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=[response(bad),response(good)]) as post:
            result=run_ai_review(p,key='fake-step-correction',design=True)
        correction=json.loads(post.call_args.args[0]['input'])
        message=correction['validation_errors'][0]['message']
        self.assertIn('investigate returned 0 steps',message)
        self.assertIn('specific missing-fact question',message)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['coverage']['validated_actions'],result['coverage']['requested_actions'])

    def test_large_failed_review_gets_one_batched_correction_in_economy_mode(self):
        p=with_doc(project())
        original=copy.deepcopy(p.review['groups'][0])
        for number in range(2,10):
            group=copy.deepcopy(original);group['id']=f'R{number:03d}';group['rule']=f'other_{number}'
            p.review['groups'].append(group)
        calls=[]
        def response(payload,*args,**kwargs):
            calls.append(payload)
            requested=json.loads(payload['input'])
            scoped=requested.get('circuit_evidence',requested)
            value=design_result(scoped)
            if len(calls)==1:
                for action in value['actions']:action['disposition']='intentional'
            return {'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(value)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=response):
            result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['coverage']['validated_actions'],result['coverage']['requested_actions'])
        self.assertEqual(len(calls),2,'Low usage allows one combined correction')
        scopes=[json.loads(call['input'])['circuit_evidence']['action_group_ids'] for call in calls[1:]]
        self.assertEqual([len(scope) for scope in scopes],[9])

    def test_board_review_accepts_more_than_40_real_evidence_references(self):
        p=with_doc(project());packet=evidence_packet(p);value=design_result(packet)
        ids={f'C:U{number}' for number in range(100)} | allowed_evidence(packet)
        value['device_evidence_ids']=sorted(ids)
        value['actions'][0]['evidence_ids']=sorted(ids)
        validate_review(value,ids,schema=DESIGN_SCHEMA,packet=packet)
        value['device_evidence_ids'].append('C:DOES_NOT_EXIST')
        with self.assertRaises(AIError):validate_review(value,ids,schema=DESIGN_SCHEMA,packet=packet)

    def test_large_non_evidence_list_identifies_the_invalid_field(self):
        p=with_doc(project());packet=evidence_packet(p);value=design_result(packet)
        value['assumptions']=['unknown']*41
        with self.assertRaisesRegex(AIError,r'\$\.assumptions'):
            validate_review(value,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)

    def test_invalid_correction_is_rejected_without_retry_loop(self):
        p=with_doc(project());bad=design_result(evidence_packet(p));bad['actions'][0]['evidence_ids']=['R999']
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(bad)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response) as post:
            result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'failed');self.assertEqual(post.call_count,2)
        self.assertIn('R999',result['message']);self.assertNotIn('result',result)

    def test_wrong_component_citation_discards_only_that_action(self):
        p=with_doc(project())
        other=copy.deepcopy(p.review['groups'][0]);other.update(id='R002',components=['U99'],rule='other_chip')
        p.review['groups'].append(other)
        packet=evidence_packet(p);bad=design_result(packet)
        bad['actions'][1]['reason']='This other chip needs the charger pad wiring.'
        bad['summary']='U99 needs the charger pad wiring.'
        bad['answer']='Change U99 using the charger datasheet.'
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(bad)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response) as post:
            result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(post.call_count,2)
        self.assertEqual([a['group_id'] for a in result['result']['actions']],['R001'])
        self.assertEqual(result['coverage'],{'validated_actions':1,'requested_actions':2})
        self.assertNotIn('U99',result['result']['summary'])
        self.assertEqual(result['result']['answer'],'')
        self.assertEqual(result['rejected_items'][0]['group_id'],'R002')
        self.assertIn('different component',result['rejected_items'][0]['message'])
        p.ai_review=result;plan=compose_plan(p)
        fallback=next(a for a in plan['actions'] if a['group_id']=='R002')
        self.assertEqual(fallback['basis'],'Local check guidance')
        self.assertNotIn('charger pad wiring',fallback['reason'])
        self.assertEqual(fallback['citations'],[])

    def test_unsupported_intent_does_not_hide_other_verified_actions(self):
        p=with_doc(project());other=copy.deepcopy(p.review['groups'][0]);other.update(id='R002',rule='other_check')
        p.review['groups'].append(other);bad=design_result(evidence_packet(p))
        bad['actions'][1]['disposition']='intentional'
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(bad)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response):result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(len(result['result']['actions']),1)
        self.assertIn('without your explanation',result['rejected_items'][0]['message'])

    def test_new_concern_is_corrected_without_repeating_accepted_actions(self):
        p=with_doc(project());packet=evidence_packet(p);good=design_result(packet)
        pin=packet['components'][0]['pins'][0]
        ref=packet['components'][0]['reference']
        self.assertEqual(ref,'U42')
        eid=good['actions'][0]['citations'][0]['evidence_id']
        # The documented evidence contract requires every cited D: ID to be
        # listed in evidence_ids; the fixture complies so the test pins the
        # correction flow, not the omission. The repair itself is covered in
        # test_ai_review.CorrectionTests.
        good['actions'][0]['evidence_ids'].append(eid)
        finding={'title':'Charger ground','check_type':'supply','reference':ref,
            'requirement':'Connect the exposed pad to VSS.','reason':'Supply return.','use_case':'','missing_context':[],
            'observations':[{'reference':ref,'pin':pin['number'],'net':pin['net']}],
            'steps':['Connect U42 exposed pad to VSS.'],'evidence_ids':['C:U42',eid],
            'citations':good['actions'][0]['citations']}
        good['circuit_findings']=[finding]
        validate_review(good,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        bad=copy.deepcopy(good);bad['circuit_findings'][0]['steps']=[]
        corrected=copy.deepcopy(good);corrected['actions']=[]
        response=lambda v:{'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(v)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=[response(bad),response(corrected)]) as post:
            result=run_ai_review(p,key='fake-new-concern',design=True)
        self.assertEqual(post.call_count,2)
        sent=json.loads(post.call_args.args[0]['input'])
        self.assertEqual(sent['rejected_plan']['actions'],[])
        self.assertEqual(sent['rejected_plan']['circuit_findings'],bad['circuit_findings'])
        self.assertTrue(sent['circuit_evidence']['datasheet_excerpts'])
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual(result['rejected_items'],[])
        self.assertEqual(result['result']['actions'],good['actions'])
        self.assertEqual(result['result']['circuit_findings'],good['circuit_findings'])
        # A provider silently dropping the concern is not evidence it was resolved.
        from pcb_checker.ai.reviewer import _REVIEW_CACHE
        _REVIEW_CACHE.clear()
        omitted=copy.deepcopy(corrected);omitted['circuit_findings']=[]
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=[response(bad),response(omitted)]):
            result=run_ai_review(p,key='fake-new-concern',design=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['rejected_items'][0]['reference'],'U42')
        self.assertEqual(result['result']['circuit_findings'],[])
        self.assertEqual(result['result']['actions'],good['actions'])

    def test_invented_new_pin_connection_is_excluded(self):
        p=with_doc(project());bad=design_result(evidence_packet(p));eid=bad['actions'][0]['citations'][0]['evidence_id']
        bad['circuit_findings']=[{'title':'Imaginary wire','check_type':'supply','reference':'U42',
            'requirement':'Ground the chip.','reason':'Supply return.','use_case':'','missing_context':[],
            'observations':[{'reference':'U42','pin':'999','net':'INVENTED'}],
            'steps':['Change it.','Check it.'],'evidence_ids':['C:U42',eid],
            'citations':bad['actions'][0]['citations']}]
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(bad)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response):result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'completed');self.assertEqual(result['result']['circuit_findings'],[])
        self.assertEqual(len(result['result']['actions']),1)
        self.assertEqual(result['rejected_items'][0]['reference'],'U42')

    def test_verify_step_retry_ends_with_no_user_homework(self):
        import copy, json
        from unittest.mock import patch
        from pcb_checker.ai.reviewer import run_ai_review
        p=with_doc(project());packet=evidence_packet(p);good=design_result(packet)
        bad=copy.deepcopy(good)
        bad['actions'][0]['steps']=['Confirm SW9 mounting tabs are mechanical only - no wire change yet.']
        response=lambda v:{'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(v)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',side_effect=[response(bad),response(good)]) as post:
            result=run_ai_review(p,key='fake-sw9-retry',design=True)
        self.assertEqual(post.call_count,2)
        self.assertEqual(result['status'],'completed',result)
        shown=[s for a in result['result']['actions'] for s in a['steps']]
        self.assertTrue(shown)
        self.assertFalse(any('Confirm SW9' in s for s in shown))
        self.assertFalse(any(q for q in result['result']['questions'] if 'SW9' in q))

    def test_finding_missing_context_delegation_is_rejected(self):
        from pcb_checker.ai.reviewer import evidence_packet
        p=with_doc(project());packet=evidence_packet(p);good=design_result(packet)
        pin=packet['components'][0]['pins'][0];ref=packet['components'][0]['reference']
        eid=good['actions'][0]['citations'][0]['evidence_id']
        base={'title':'Mounting check','check_type':'supply','reference':ref,
            'requirement':'Mounting tabs are mechanical only.','reason':'Footprint.','use_case':'',
            'missing_context':[],'observations':[{'reference':ref,'pin':pin['number'],'net':pin['net']}],
            'steps':['Record the verified mechanical-only tabs with the datasheet evidence.'],'evidence_ids':['C:'+ref,eid],
            'citations':good['actions'][0]['citations']}
        good['circuit_findings']=[base]
        validate_review(good,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        bad=copy.deepcopy(good)
        bad['circuit_findings'][0]['missing_context']=['Confirm the SW9 mounting tabs are mechanical only.']
        with self.assertRaises(AIError) as caught:
            validate_review(bad,allowed_evidence(packet),schema=DESIGN_SCHEMA,packet=packet)
        self.assertIn('delegates the supplied circuit analysis',str(caught.exception))

    def test_unverified_device_interpretation_still_rejects_entire_review(self):
        p=with_doc(project());bad=design_result(evidence_packet(p));bad['device_evidence_ids']=['C:UNKNOWN']
        response={'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(bad)}]}]}
        with patch('pcb_checker.ai.reviewer.post_ai',return_value=response):result=run_ai_review(p,key='test-secret',design=True)
        self.assertEqual(result['status'],'failed');self.assertNotIn('result',result)

    def test_context_validation_rejects_unknown_refs_and_findings(self):
        p=project()
        for change in [{'description':123},{'intention':{'key':'missing','note':'yes'}},
                       {'part_choice':{'reference':'U42','code':'https://evil.test'}},
                       {'part_choice':{'reference':'U9999','code':'C150772'}}]:
            with self.subTest(change=change),self.assertRaises(ValueError):edit_context(p,empty_context(),change)

    def test_context_revision_conflicts_and_project_separation(self):
        p=project()
        with tempfile.TemporaryDirectory() as td:
            store=WorkspaceStore(td);saved=store.save_context(context_key(p),empty_context(),0)
            self.assertEqual(saved['revision'],1)
            with self.assertRaises(RevisionConflict):store.save_context(context_key(p),empty_context(),0)
            p.provenance['project_name']='different board';self.assertIsNone(store.design_context(context_key(p)))


class RethinkTests(unittest.TestCase):
    def test_context_update_creates_snapshot_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as td:
            p=project();folder=save_report(p,td);state=AppState(td,td);state.open_report(folder.name)
            prior=state.report_id;g=keyed_groups(asdict(state.project))[0]
            state.annotate({'report_id':prior,'key':g['key'],'status':'reviewed','note':'Journal note'})
            with patch('pcb_checker.webapp.analyze_project') as analysis,patch('pcb_checker.webapp.run_ai_review') as ai:
                state.start('rethink',{'report_id':prior,'context_revision':0,'description':'A battery tester.', 'operating_context':{'power':'One cell'},
                                      'intention':{'key':g['key'],'note':'External wiring provides this function.'}})
                state.worker.join(5)
            self.assertEqual(state.job['status'],'completed');analysis.assert_not_called();ai.assert_not_called()
            self.assertNotEqual(state.report_id,prior)
            self.assertEqual(state.project_data()['action_plan']['device']['description'],'A battery tester.')
            self.assertEqual(state.store.decisions(state.report_id)[g['key']]['note'],'Journal note')
            self.assertEqual(state.read_report(prior)['design_context'],empty_context())
            self.assertEqual(state.project.design_context['revision'],1)
            state.close();fresh=AppState(td,td)
            self.assertEqual(fresh.project_data()['workspace']['design_context']['description'],'A battery tester.')
            self.assertEqual(fresh.project_data()['workspace']['design_context']['operating_context']['power'],'One cell')
            with self.assertRaises(AppError):fresh.start('rethink',{'report_id':prior,'context_revision':1})
            with self.assertRaises(AppError):fresh.start('rethink',{'report_id':fresh.report_id,'context_revision':0})
            fresh.close()

    def test_rethink_uses_current_context_for_ai_and_keeps_notes_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            p=project();folder=save_report(p,td);state=AppState(td,td,key='test-secret');state.open_report(folder.name)
            with patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'503'}) as ai:
                state.start('rethink',{'report_id':state.report_id,'context_revision':0,'description':'External display.', 'ai':True})
                state.worker.join(5)
            self.assertEqual(ai.call_args.args[0].design_context['description'],'External display.')
            self.assertTrue(ai.call_args.kwargs['design'])
            self.assertEqual(state.project.action_plan['status'],'local')
            self.assertEqual(state.project.ai_review['status'],'failed');state.close()


class ContextRefreshTests(unittest.TestCase):
    def stored(self, revision, **overrides):
        base = {**empty_context(), 'revision': revision,
                'product_questions': {'connector:J1': 'What does J1 connect to?'}}
        base.update(overrides)
        return base

    def run_project(self, revision=0):
        p = project()
        p.design_context = {**empty_context(), 'revision': revision}
        return p

    def test_cosmetic_question_drift_is_adopted(self):
        p = self.run_project(0)
        store = {context_key(p): self.stored(1)}
        fake = type('Store', (), {'design_context': lambda self, key: store.get(key)})()
        self.assertTrue(refresh_context_from_store(p, fake))
        self.assertEqual(p.design_context['revision'], 1)
        self.assertIn('connector:J1', p.design_context['product_questions'])

    def test_mid_run_user_answer_keeps_snapshot(self):
        p = self.run_project(0)
        stored = self.stored(1, product_context={'experience': 'beginner'})
        store = {context_key(p): stored}
        fake = type('Store', (), {'design_context': lambda self, key: store.get(key)})()
        self.assertFalse(refresh_context_from_store(p, fake))
        self.assertEqual(p.design_context.get('revision'), 0)
        self.assertNotIn('product_context', p.design_context)

    def test_matching_revision_refreshes_nothing(self):
        p = self.run_project(2)
        store = {context_key(p): self.stored(2)}
        fake = type('Store', (), {'design_context': lambda self, key: store.get(key)})()
        self.assertFalse(refresh_context_from_store(p, fake))
        self.assertEqual(p.design_context.get('revision'), 2)

    def test_missing_store_context_refreshes_nothing(self):
        p = self.run_project(0)
        fake = type('Store', (), {'design_context': lambda self, key: None})()
        self.assertFalse(refresh_context_from_store(p, fake))

    def test_real_store_shape_with_empty_answers_is_adopted(self):
        # Real store rows carry explicit product_context:{} while a fresh run
        # snapshot built from empty_context() omits the key. {} vs missing
        # must not read as a substantive change.
        p = self.run_project(0)
        self.assertNotIn('product_context', p.design_context)
        stored = {'revision': 1, 'description': '', 'operating_context': {},
                  'intentions': {}, 'part_choices': {}, 'product_context': {},
                  'product_questions': {'connector:J1': 'What does J1 connect to?'}}
        store = {context_key(p): stored}
        fake = type('Store', (), {'design_context': lambda self, key: store.get(key)})()
        self.assertTrue(refresh_context_from_store(p, fake))
        self.assertEqual(p.design_context['revision'], 1)


if __name__=='__main__':unittest.main()
