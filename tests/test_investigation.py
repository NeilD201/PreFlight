"""Offline integration checks: no live provider requests or project-file writes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai import investigation as workflow, reviewer, circuit_audit
from pcb_checker.ai.usage import request_budget, current_budget
from pcb_checker import board_edits
from test_circuit_audit import fixture, response


def plan(tool='read_datasheet', references=None, question='What does SDA require?'):
    return {'product_hypothesis': 'A sensor connected to a controller.', 'tasks': [
        {'tool': tool, 'references': references or ['U1'], 'question': question,
         'reason': 'Establish the interface requirements before recommending a wire change.'}]}


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.project = fixture()
        self.packet = reviewer.evidence_packet(self.project)
        reviewer._REVIEW_CACHE.clear()

    def test_unknown_tools_and_unknown_components_never_execute(self):
        for value in (plan('run_shell'), plan(references=['U999']), plan(references=['U1','U1'])):
            with self.subTest(value=value), self.assertRaises(reviewer.AIError):
                workflow.validate_plan(value, self.packet)
        self.project.components['U1'].schematic[0].dnp=True
        with self.assertRaises(reviewer.AIError):
            workflow.validate_plan(plan(), reviewer.evidence_packet(self.project))

    def test_duplicate_tasks_and_empty_plans_rejected(self):
        value=plan();value['tasks']*=2
        with self.assertRaises(reviewer.AIError):workflow.validate_plan(value,self.packet)
        value['tasks']=[]
        with self.assertRaises(reviewer.AIError):workflow.validate_plan(value,self.packet)

    def test_trace_contains_receiving_part_and_pullup(self):
        task=plan('trace_connections')['tasks'][0]
        data, gaps, _=workflow.execute(task,self.project,self.packet,[])
        self.assertEqual(set(data['component_ids']),{'C:U1','C:U2','C:R1'})
        self.assertIn('routing_chain_ids',data)

    def test_search_reads_new_page_and_keeps_source_identity(self):
        self.project.datasheets['documents']['C123']['pages'].append(
            {'page':9,'text':'Unique thermal shutdown threshold is specified here.'})
        docs=workflow.retrieve_passages(self.project,['U1'],'thermal shutdown threshold')
        self.assertEqual(docs[0]['page'],9)
        self.assertEqual(docs[0]['references'],['U1'])
        self.assertEqual(docs[0]['sha256'],'a'*64)
        self.assertIn('D:C123:p9:',docs[0]['evidence_id'])

    def test_provisional_and_missing_sources_stay_limited(self):
        self.project.datasheets['parts'][0]['status']='provisional'
        task=plan(references=['U1','U2'])['tasks'][0]
        _, gaps, docs=workflow.execute(task,self.project,self.packet,[])
        self.assertTrue(any('U2' in g for g in gaps))
        self.assertTrue(any('provisional' in g for g in gaps))
        self.assertEqual(docs[0]['part_match'],'provisional')

    def test_search_parts_names_sourced_candidate_with_citable_passage(self):
        task = plan('search_parts', references=['U1'],
                    question='USB TVS array SRV05 for D+ D- protection')['tasks'][0]
        workflow.validate_plan({'product_hypothesis': 'x', 'tasks': [task]}, self.packet)
        client = MagicMock()
        client.search.return_value = [{'mpn': 'SRV05-4', 'code': 'C282836',
                                       'manufacturer': 'WeiFeng', 'package': 'SOT-23-6',
                                       'description': 'TVS diode array'}]
        client.document.return_value = {'pdf_url': 'https://example.test/srv05.pdf', 'sha256': 'b' * 64,
                                        'pages': [{'page': 1, 'text': 'SRV05-4 TVS diode array protects USB D+ D- lines at 5V working voltage.'}]}
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertEqual(data['candidates'][0]['mpn'], 'SRV05-4')
        self.assertEqual(data['candidates'][0]['code'], 'C282836')
        self.assertTrue(retrieved, 'A fetched candidate passage must be citable.')
        self.assertTrue(all(d['evidence_id'].startswith('D:C282836:') for d in retrieved))
        self.assertTrue(all(d['references'] == ['U1'] for d in retrieved))
        self.assertIn('SRV05-4', retrieved[0]['text'])
        client.document.assert_called_once_with('C282836')

    def test_search_parts_without_match_never_invents_a_part(self):
        task = plan('search_parts', references=['U1'], question='unobtainium hyperdiode XJ-999')['tasks'][0]
        client = MagicMock()
        client.search.return_value = []
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertEqual(data['candidates'], [])
        self.assertEqual(retrieved, [])
        self.assertTrue(any('No catalog candidate' in g for g in gaps))
        client.document.assert_not_called()

    def test_search_parts_extracts_family_token_from_sentence(self):
        task = plan('search_parts', references=['U1'],
                    question='Which TVS array of the SRV05 family protects USB D+ D-?')['tasks'][0]
        client = MagicMock()
        cand = [{'mpn': 'SRV05-4', 'code': 'C2836319',
                 'manufacturer': '', 'package': '', 'description': ''}]
        client.search.side_effect = lambda query: list(cand) if query == 'SRV05' else []
        client.document.return_value = {'pdf_url': '', 'sha256': 'c' * 64, 'pages': []}
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertEqual(client.search.call_args_list[0].args[0], 'SRV05')
        self.assertEqual(data['searched'], 'SRV05')
        self.assertEqual(data['candidates'][0]['mpn'], 'SRV05-4')

    def test_search_parts_prefers_ratings_over_disclaimers(self):
        task = plan('search_parts', references=['U1'],
                    question='Which TVS array protects USB D+ D-?')['tasks'][0]
        client = MagicMock()
        client.search.side_effect = lambda query: [{'mpn': 'SRV05-4', 'code': 'C1',
                                                     'manufacturer': '', 'package': '', 'description': ''}] if query == 'SRV05' else []
        pages = [{'page': 6, 'text': 'SRV05-4 USB TVS array protects D+ D-. ' + ('Any and all products do not have specifications for life-support or aircraft. ' * 40)},
                 {'page': 4, 'text': 'SRV05-4 Symbol Parameters VRWM Peak Reverse Working Voltage 5V VC Clamp Voltage 12V SOT-23-6 package pin 1 IO1.'}]
        client.document.return_value = {'pdf_url': '', 'sha256': 'e' * 64, 'pages': pages}
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertTrue(retrieved, 'A ratings passage must be fetched.')
        self.assertTrue(all('life-support' not in d['text'] for d in retrieved),
                        'Disclaimer pages must not become citable evidence.')
        self.assertTrue(any('VRWM' in d['text'] for d in retrieved),
                        'The ratings passage must be preferred.')

    def test_search_parts_falls_back_to_known_protection_families(self):
        task = plan('search_parts', references=['U1'],
                    question='Which TVS array protects USB D+ D-?')['tasks'][0]
        client = MagicMock()
        cand = [{'mpn': 'PRTR5V0U2X', 'code': 'C2827688',
                 'manufacturer': '', 'package': '', 'description': ''}]
        client.search.side_effect = lambda query: list(cand) if query == 'PRTR5V0U2X' else []
        client.document.return_value = {'pdf_url': '', 'sha256': 'd' * 64, 'pages': []}
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertEqual(client.search.call_args_list[0].args[0], 'SRV05')
        self.assertEqual(data['searched'], 'PRTR5V0U2X')
        self.assertEqual(data['candidates'][0]['mpn'], 'PRTR5V0U2X')

    def test_search_parts_failure_degrades_to_gaps_not_crash(self):
        from pcb_checker.datasheets import DatasheetError
        task = plan('search_parts', references=['U1'], question='USB TVS array SRV05')['tasks'][0]
        client = MagicMock()
        client.search.side_effect = DatasheetError('LCSC search is unavailable')
        with patch('pcb_checker.datasheets.LCSCClient', return_value=client):
            data, gaps, retrieved = workflow.execute(task, self.project, self.packet, [], datasheet_cache='/tmp')
        self.assertEqual(data['candidates'], [])
        self.assertEqual(retrieved, [])
        self.assertTrue(gaps)

    def test_plan_requires_mpns_cited_not_invented(self):
        self.assertIn('Name an exact', workflow.INSTRUCTIONS)
        self.assertIn('D: citation', workflow.INSTRUCTIONS)

    def test_candidate_naming_required_only_where_excerpts_fit(self):
        packet = {'review_groups': [
                {'id': 'R006', 'components': ['J1', 'R4'], 'decision_constraint': 'Use investigate.'},
                {'id': 'R009', 'components': ['U3'], 'decision_constraint': 'Use investigate.'}],
            'datasheet_excerpts': [
                {'evidence_id': 'D:C1:p4:0', 'mpn': 'SRV05-4', 'candidate': True,
                 'references': ['J1'], 'text': 'VRWM 5V.'},
                {'evidence_id': 'D:F1:p1:0', 'mpn': 'LD1117V',
                 'references': ['U3'], 'text': 'TAB is VOUT.'}]}
        out = workflow.require_candidate_naming(copy.deepcopy(packet))
        r006 = next(g for g in out['review_groups'] if g['id'] == 'R006')
        r009 = next(g for g in out['review_groups'] if g['id'] == 'R009')
        self.assertIn('SRV05-4', r006['decision_constraint'])
        self.assertIn('verbatim D: quote', r006['decision_constraint'])
        self.assertNotIn('Candidate D: excerpts', r009['decision_constraint'],
                         'Fitted-part datasheets must not force a catalog prescription.')
        # Idempotent: investigation paths must not stack the requirement.
        twice = workflow.require_candidate_naming(out)['review_groups'][0]['decision_constraint']
        self.assertEqual(twice.count('Candidate D: excerpts'), 1)

    def test_stale_calculations_cannot_be_reported_as_current(self):
        task=plan('calculate_limits')['tasks'][0]
        rows=[{'key':'i2c:BUS','label':'BUS','status':'checked','stale':True,'metrics':{'ok':1}}]
        data,gaps,_=workflow.execute(task,self.project,self.packet,rows)
        self.assertEqual(data['results'],[])
        self.assertTrue(gaps)
        rows[0].pop('stale')
        data,gaps,_=workflow.execute(task,self.project,self.packet,rows)
        self.assertEqual(data['results'],rows)
        self.assertEqual(gaps,[])

    def test_planning_failure_preserves_normal_review_input_and_redacts_key(self):
        with patch.object(workflow,'ask_plan',side_effect=reviewer.AIError('failed secret-key')):
            packet,trace,_=workflow.investigate(self.project,self.packet,key='secret-key',model='test',provider='gemini')
        self.assertEqual(packet,self.packet)
        self.assertEqual(trace['status'],'limited')
        self.assertTrue(trace['ledger']['claims'])
        self.assertTrue(any(e['kind']=='plan_unavailable' for e in trace['ledger']['events']))
        self.assertNotIn('secret-key',json.dumps(trace))

    def test_low_budget_does_not_spend_request_on_plan(self):
        # The single complete tier grants a full allowance, so a fresh budget is
        # no longer "low". Simulate prior spend down to the <3-request reserve.
        with request_budget() as budget:
            while budget.limit - budget.requests >= 3:
                budget.consume()
            with patch.object(workflow,'ask_plan') as ask:
                _,trace,_=workflow.investigate(self.project,self.packet,key='fake',model='test',provider='gemini')
        ask.assert_not_called()
        self.assertEqual(trace['status'],'skipped')

    def test_invalid_credentials_do_not_start_planning_or_review_requests(self):
        with patch.object(workflow,'ask_plan') as planner, patch.object(reviewer,'post_ai') as send:
            result=reviewer.run_ai_review(self.project,key='invalid key',model='meta/muse-spark-1.3-contributor',design=True,deep=True)
        planner.assert_not_called()
        send.assert_not_called()
        self.assertEqual(result['status'],'not_configured')

    def test_failed_planner_is_not_cached_as_finished_investigation(self):
        result={'status':'completed','result':{},'pipeline':{'status':'unavailable'},
                'circuit_audit':{'status':'completed'},'usage':{},'elapsed_seconds':0}
        with patch.object(reviewer,'_run_ai_review',side_effect=lambda *a,**k:copy.deepcopy(result)) as run:
            for _ in range(2):reviewer.run_ai_review(self.project,key='fake',design=True,deep=True)
        self.assertEqual(run.call_count,2)

    def test_meta_transport_uses_structured_planner_response(self):
        native={'choices':[{'message':{'role':'assistant','content':json.dumps(plan())},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120}}
        with patch.object(reviewer,'_request',return_value=native) as send:
            value,usage=workflow.ask_plan(self.packet,key='fake',model='meta/muse-spark-1.3-contributor',provider='gemini',progress=None)
        self.assertEqual(workflow.validate_plan(value,self.packet),plan())
        self.assertEqual(send.call_args.args[0],'https://api.meta.ai/v1/chat/completions')
        self.assertEqual(send.call_args.args[1]['response_format']['type'],'json_schema')
        self.assertEqual(usage['total_input_tokens'],100)

    def test_detailed_review_passes_tools_to_review_and_audits_under_shared_budget(self):
        captured=[]
        def planner(*args,**kwargs):
            current_budget().consume()
            return plan(),{'total_input_tokens':10}
        def interpret(*args,**kwargs):
            current_budget().consume()
            captured.append(kwargs['investigation_packet'])
            return {'status':'completed','result':{'actions':[]},'model':'meta/muse-spark-1.3-contributor',
                    'usage':{'input_tokens':20},'elapsed_seconds':0,'coverage':{'validated_actions':0,'requested_actions':0}}
        def audit_answer(scope,**kwargs):
            current_budget().consume()
            return response(scope),{'total_input_tokens':30}
        with patch.object(workflow,'ask_plan',side_effect=planner), patch.object(reviewer,'_run_issue_review',side_effect=interpret), \
             patch.object(circuit_audit,'_ask',side_effect=audit_answer):
            result=reviewer.run_ai_review(self.project,key='fake',model='test',provider='meta',design=True,deep=True)
        self.assertTrue(captured[0]['investigation']['tool_results'])
        self.assertEqual(result['usage']['input_tokens'],90)
        self.assertEqual(result['request_usage']['requests'],4)
        self.assertEqual(result['request_usage']['request_limit'],12)
        self.assertEqual(result['pipeline']['status'],'limited') # U2 lacks a datasheet.
        self.assertTrue(result['circuit_audit']['evidence_retrieval'])
        self.assertNotIn('fake',json.dumps(result['pipeline']))

    def test_investigation_crash_preserves_review_with_unavailable_pipeline(self):
        def interpret(*args,**kwargs):
            return {'status':'completed','result':{'actions':[]},'model':'m',
                    'usage':{},'elapsed_seconds':0,'coverage':{'validated_actions':0,'requested_actions':0}}
        audit={'status':'completed','target_count':0,'reviewed_count':0,'unresolved_count':0,
               'findings':[],'usage_samples':[],'evidence_retrieval':[],'elapsed_seconds':0}
        with patch('pcb_checker.ai.investigation.investigate',
                   side_effect=reviewer.AIError('boom secret-X')), \
             patch.object(reviewer,'_run_issue_review',side_effect=interpret), \
             patch.object(circuit_audit,'run_circuit_audit',return_value=audit):
            result=reviewer.run_ai_review(self.project,key='secret-X',model='m',provider='meta',design=True,deep=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['pipeline']['status'],'unavailable')
        self.assertTrue(any(e['kind']=='investigation_unavailable' for e in result['pipeline']['ledger']['events']))
        self.assertNotIn('secret-X',json.dumps(result['pipeline']))

    def test_audit_crash_preserves_review_pipeline_and_marks_audit_unavailable(self):
        def interpret(*args,**kwargs):
            return {'status':'completed','result':{'actions':[]},'model':'m',
                    'usage':{},'elapsed_seconds':0,'coverage':{'validated_actions':0,'requested_actions':0}}
        pipeline={'status':'limited','steps':[],'tasks':[],'elapsed_seconds':0,
                  'ledger':{'claims':[],'events':[]},'limitations':[]}
        with patch('pcb_checker.ai.investigation.investigate',return_value=(None,pipeline,{})), \
             patch.object(reviewer,'_run_issue_review',side_effect=interpret), \
             patch.object(circuit_audit,'run_circuit_audit',side_effect=reviewer.AIError('audit boom')):
            result=reviewer.run_ai_review(self.project,key='fake',model='m',provider='meta',design=True,deep=True)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(result['circuit_audit']['status'],'unavailable')
        self.assertEqual(result['result']['circuit_findings'],[])
        self.assertTrue(any('audit' in entry for entry in result['result']['limitations']))
        self.assertIn('coverage_proof',result['pipeline'])

    def test_supported_claims_ride_to_the_review_packet(self):
        def planner(*args, **kwargs):
            return plan(), {}
        with patch.object(workflow, 'ask_plan', side_effect=planner):
            expanded, trace, _ = workflow.investigate(self.project, self.packet, key='fake', model='test',
                                                      provider='gemini')
        carried = expanded['investigation']['material_claims']
        self.assertTrue(carried)
        for claim in carried:
            self.assertEqual(set(claim), {'id', 'statement', 'status', 'scope'})
            self.assertEqual(claim['status'], 'supported')
        ledger_ids = {c['id'] for c in trace['ledger']['claims'] if c['status'] == 'supported'}
        self.assertEqual({c['id'] for c in carried}, ledger_ids)

    def test_netless_evidence_packet_records_degraded_limitation(self):
        packet = {'components': [{'reference': 'U1', 'value': 'X', 'footprint': '', 'mpn': '',
                                  'dnp': False, 'pins': [{'number': '1', 'name': 'P', 'net': ''}]}],
                  'nets': [], 'review_groups': [], 'datasheet_coverage': [], 'bus_candidates': [],
                  'bypass_capacitors': [], 'datasheet_excerpts': [], 'design_context': {},
                  'schematic_checks': []}
        with patch.object(workflow, 'ask_plan', side_effect=reviewer.AIError('no plan')):
            _, trace, _ = workflow.investigate(self.project, packet, key='fake', model='test',
                                               provider='gemini')
        self.assertTrue(any(e['kind'] == 'evidence_degraded' for e in trace['ledger']['events']))
        self.assertTrue(any('netlist' in entry for entry in trace['limitations']))

    def test_feedback_retrieves_missing_passage_before_challenge(self):
        scope=circuit_audit.scoped_packet(self.packet,['U1'],circuit_audit.functional_documents(self.project,['U1']))
        self.project.datasheets['documents']['C123']['pages'].append({'page':12,'text':'Mode retention sleep behavior requires retained power.'})
        raw=response(scope);raw['parts'][0]['checks'][0]['comparison']['requirement']='mode retention sleep behavior'
        enriched,extra,searches=workflow.enrich_scope(self.project,scope,raw)
        self.assertTrue(any(d['page']==12 for d in extra))
        self.assertGreater(len(enriched['datasheet_excerpts']),len(scope['datasheet_excerpts']))
        self.assertGreater(searches[0]['new_passages'],0)

    def test_revision_fingerprint_and_persistence_do_not_contain_credentials(self):
        self.project.provenance={'source_hashes':{'board.kicad_pcb':'123'}}
        with patch.object(workflow,'ask_plan',return_value=(plan(),{})):
            _,trace,_=workflow.investigate(self.project,self.packet,key='private-key',model='test',provider='gemini')
        self.assertEqual(trace['source_hashes'],self.project.provenance['source_hashes'])
        self.assertNotIn('private-key',json.dumps(trace))
        self.assertEqual(len(trace['tasks'][0]['result_sha256']),64)

    def test_plan_timeout_is_retried_once_then_succeeds(self):
        import socket
        from unittest.mock import Mock
        from pcb_checker.ai import fault, consult, gauge
        native = {'choices': [{'message': {'role': 'assistant', 'content': json.dumps(plan())},
                                           'finish_reason': 'stop'}],
                  'usage': {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120}}
        def handle(payload):
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=None)
            response.read.return_value = json.dumps(payload).encode()
            return response
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = [socket.timeout('timed out'), handle(native)]
            value, usage = workflow.ask_plan(self.packet, key='fake',
                                             model='meta/muse-spark-1.3-contributor', provider='meta', progress=None)
        self.assertEqual(value, plan())
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(usage['total_input_tokens'], 100)

    def test_plan_timeout_is_bounded_after_two_attempts(self):
        import socket
        with patch('pcb_checker.ai.reviewer.build_opener') as opener, \
             patch('pcb_checker.ai.reviewer.time.sleep'):
            opener.return_value.open.side_effect = socket.timeout('timed out')
            with self.assertRaises(reviewer.AIError) as caught:
                workflow.ask_plan(self.packet, key='fake', model='meta/muse-spark-1.3-contributor',
                                  provider='meta', progress=None)
        self.assertEqual(opener.return_value.open.call_count, 2)
        self.assertEqual(caught.exception.diagnostics['error_kind'], 'timeout')

    def test_discovery_crash_preserves_ledger_and_continues_pipeline(self):
        from pcb_checker.ai import fault, consult, gauge, design_scan
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})), \
             patch.object(design_scan, 'discover', side_effect=ValueError('broken topology')), \
             patch.object(fault, 'challenge', return_value={}), \
             patch.object(consult, 'consult', return_value={}), \
             patch.object(gauge, 'audit', return_value={}):
            _, trace, _ = workflow.investigate(self.project, self.packet, key='fake', model='test',
                                              provider='gemini')
        kinds = [e['kind'] for e in trace['ledger']['events']]
        self.assertIn('discovery_unavailable', kinds)
        # Atlas discovery ran before the crash; its events survive on the same ledger.
        self.assertTrue(any(k != 'discovery_unavailable' for k in kinds))
        self.assertTrue(any('broken topology' in entry for entry in trace['limitations']))
        self.assertIn(trace['status'], ('completed', 'limited'))

    def test_live_publish_reports_partial_pipeline_without_disturbing_run(self):
        from pcb_checker.ai import fault, consult, gauge
        snapshots = []
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})), \
             patch.object(fault, 'challenge', return_value={}), \
             patch.object(consult, 'consult', return_value={}), \
             patch.object(gauge, 'audit', return_value={}):
            _, trace, _ = workflow.investigate(self.project, self.packet, key='fake', model='test',
                                              provider='gemini', publish=snapshots.append)
        self.assertTrue(snapshots)
        for snapshot in snapshots:
            self.assertIn('ledger', snapshot)
            self.assertIn('coverage_proof', snapshot)
            self.assertIn('replay', snapshot)
            self.assertTrue(all(isinstance(e.get('seq', 0), int) for e in snapshot['ledger']['events']))
        self.assertNotIn('fake', json.dumps(snapshots))
        self.assertIn(trace['status'], ('completed', 'limited'))

    def test_routed_delegation_executes_and_returns_evidence(self):
        routed = {'id': 'qc-test1', 'tool': 'trace_connections', 'references': ['U1'],
                  'question': 'Which net connects U1?', 'reason': 'Establish connectivity.',
                  'origin_agent': 'circuit', 'need': 'Which net connects U1?'}
        with patch.object(workflow, 'ask_plan', return_value=(plan(), {})), \
             patch('pcb_checker.ai.fault.challenge', return_value={}), \
             patch('pcb_checker.ai.consult.consult', return_value={}), \
             patch('pcb_checker.ai.gauge.audit', return_value={}), \
             patch('pcb_checker.ai.reverify.verify_all', return_value={}):
            packet, trace, _ = workflow.investigate(
                self.project, self.packet, key='k', model='t', provider='m', extra_tasks=[routed])
        self.assertIn('qc-test1', packet.get('delegation_results', {}))
        self.assertEqual(packet['delegation_results']['qc-test1']['origin_agent'], 'circuit')
        self.assertTrue(any(e['kind'] == 'delegations_admitted' for e in trace['ledger']['events']))
        entry = next(t for t in trace['tasks'] if t['id'] == 'qc-test1')
        self.assertIn(entry['status'], ('evidence_gathered', 'limited'))
        self.assertTrue(entry.get('limitations') is not None)

    def test_routed_delegation_validation_never_breaks_planned_work(self):
        good = {'id': 'qc-good', 'tool': 'trace_connections', 'references': ['U1'],
                'question': 'Which net connects U1?', 'reason': 'Establish connectivity.',
                'origin_agent': 'circuit', 'need': 'Which net connects U1?'}
        dup = dict(good, id='qc-dup')
        bad_tool = dict(good, id='qc-bad', tool='run_shell')
        bad_ref = dict(good, id='qc-ref', references=['U999'])
        admitted, pending = workflow._admit_routed_tasks(
            [good, dup, bad_tool, bad_ref, None, 'x'],
            plan()['tasks'], self.packet)
        self.assertEqual([t['id'] for t in admitted], ['qc-good'])
        self.assertEqual(len(pending), 5)
        self.assertTrue(all(isinstance(p, str) for p in pending))

    def test_routed_specialist_tasks_never_raise(self):
        self.assertEqual(reviewer._routed_specialist_tasks(None), [])
        tasks = reviewer._routed_specialist_tasks(self.project)
        self.assertIsInstance(tasks, list)
        for task in tasks:
            self.assertIn(task['tool'], workflow.TOOLS)

    def test_repair_history_survives_disposable_draft_and_detects_same_failed_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/('a'*32)
            ops=[{'kind':'move_footprint','reference':'U1','x_mm':1,'y_mm':2,'rotation_deg':0,'reason':'a'}]
            board_edits.repair_event(folder,'drc_failed',attempt=0,hashes={'board':'old'},operations=ops,rules=['clearance'])
            ops[0]['reason']='new explanation, same edit'
            history,repeated=board_edits.repair_event(folder,'proposal',attempt=1,hashes={'board':'old'},operations=ops)
            self.assertTrue(repeated)
            self.assertFalse(folder.exists())
            self.assertEqual(len(history),2)
            _,repeated=board_edits.repair_event(folder,'proposal',attempt=1,hashes={'board':'new'},operations=ops)
            self.assertFalse(repeated)
            ops[0]['x_mm']=5
            _,repeated=board_edits.repair_event(folder,'proposal',attempt=1,hashes={'board':'old'},operations=ops)
            self.assertFalse(repeated)


if __name__=='__main__':unittest.main()
