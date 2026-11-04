import json
from pathlib import Path
import sys
import tempfile
import unittest
import io
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.repair_design import validate,safe_url,reference_candidate,SCHEMA,compare_nordic_rf_network,reference_document,REFERENCES,_radio_identity
from pcb_checker.webapp import AppState,AppError
from pcb_checker.file_fixes import digest
from test_components import schematic,symbol,board,footprint
from pcb_checker.analysis import analyze_project


# Repair context now includes parsed product evidence; use real model defaults.
def project_fixture(**fields):
    from pcb_checker.models import Project, Component
    result=Project('test',**fields)
    result.components={ref:Component(ref,schematic=getattr(c,'schematic',[]),pcb=getattr(c,'pcb',[]),
                                   schematic_pins=getattr(c,'schematic_pins',[]))
                       for ref,c in result.components.items()}
    return result


class DesignTests(unittest.TestCase):
    def test_rf_anchor_identity_is_read_from_parsed_records(self):
        component=SimpleNamespace(
            value=None,footprint=None,
            schematic=[SimpleNamespace(value='nRF52840',footprint='Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm',properties={'Description':'2.4 GHz SoC'})],
            pcb=[SimpleNamespace(value='nRF52840',footprint='Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm',properties={})])
        project=project_fixture(components={'U13':component})
        identity=_radio_identity(project,{'anchor':'U13'})
        self.assertIn('nRF52840',identity)
        self.assertIn('AQFN-73',identity)
        self.assertIn('2.4 GHz SoC',identity)

    def test_reference_package_reads_only_expected_official_nordic_schematic(self):
        member=REFERENCES['nordic-qiaa-config1']['member']
        zipped=io.BytesIO()
        with zipfile.ZipFile(zipped,'w') as archive:archive.writestr(member,b'%PDF-test')
        class Response:
            url=REFERENCES['nordic-qiaa-config1']['url']
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return zipped.getvalue()
        class Opener:
            def open(self,*args,**kwargs):return Response()
        with tempfile.TemporaryDirectory() as temp, \
             patch('pcb_checker.repair_design.build_opener',return_value=Opener()), \
             patch('pcb_checker.repair_design.subprocess.run',return_value=SimpleNamespace(stdout=json.dumps({'pages':[{'page':1,'text':'C3 0.8pF L1 4.7nH RF C4 0.5pF'}]}))):
            doc=reference_document('nordic-qiaa-config1',Path(temp))
        self.assertEqual(doc['member'],member)
        self.assertIn('C3 0.8pF',doc['pages'][0]['text'])

    def test_http_503_uses_package_specific_nordic_comparison_not_foreign_antenna(self):
        from types import SimpleNamespace
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker.repair_design import investigate
        with tempfile.TemporaryDirectory() as temp:
            path={'id':'rf-U13','kind':'rf','nets':[{'net':'ANT'}, {'net':'MATCHED_FEED'}]}
            component=SimpleNamespace(pcb=[])
            project=project_fixture(components={'U13':component},datasheets={'parts':[{'reference':'U13','pdf_status':'read'}],'documents':{}},
                signal_paths={'paths':[path],'board_measurements':{'stackup':{}}},
                manufacturing={},design_context={},provenance={})
            packet={'datasheet_excerpts':[],'relevant_signal_paths':[path],'components':[]}
            quote='C3 0.8pF L1 4.7nH RF C4 0.5pF'
            document={'url':'https://nsscprodmedia.blob.core.windows.net/reference.zip','title':'Nordic QIAA config1','sha256':'test',
                      'pages':[{'page':1,'text':quote}]}
            with patch('pcb_checker.step_help._packet',return_value=packet), \
                 patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
                 patch('pcb_checker.repair_design.reference_document',return_value=document), \
                 patch('pcb_checker.repair_design.compare_nordic_rf_network',return_value={'matches_reference':True,'output_net':'MATCHED_FEED'}), \
                 patch('pcb_checker.repair_design.request_design',side_effect=AIError('HTTP 503',diagnostics={'http_status':503,'retryable':True})):
                result=investigate(project,{'id':'R1','components':['U13'],'signal_path_id':'rf-U13'},
                    'Design an RF repair',{},Path(temp),'key','gemini',source=None)
        self.assertEqual(result['antenna_feed_net'],'MATCHED_FEED')
        self.assertIn('not proof the antenna is absent',result['diagnosis'])
        self.assertIn('proposal',result['service_message'])
        self.assertTrue(result['questions'])
        self.assertEqual(result['reference_antenna_footprints'],[])
        self.assertIn('Keep the existing RF network',result['approach'])
        validate({k:v for k,v in result.items() if k in SCHEMA['properties']},result['sources'])

    def test_reference_fallback_needs_matching_package_evidence_and_preserves_unknowns(self):
        self.assertIsNone(reference_candidate({}))
        quote='C3 0.8pF L1 4.7nH RF C4 0.5pF'
        sources=[{'evidence_id':'REF:nordic-qiaa-config1:p1','text':quote}]
        packet={'rf_reference_comparison':{'matches_reference':True},'datasheet_excerpts':sources,
                'rf_ground_candidates':['GND'],'relevant_signal_paths':[{'kind':'rf','nets':[{'net':'ANT_RF'}]}]}
        candidate=reference_candidate(packet);validate(candidate,sources)
        self.assertEqual(len(candidate['questions']),1)
        packet['design_answers']={'antenna_environment':'unknown','rf_ground_net':'unknown'}
        candidate=reference_candidate(packet);validate(candidate,sources)
        self.assertEqual(candidate['questions'],[])
        self.assertEqual(candidate['antenna_ground_net'],'GND')
        self.assertIn('does not itself specify an antenna',candidate['approach'])

    def test_reference_only_repair_keeps_approved_sourced_antenna_candidate(self):
        sources=[{'evidence_id':'REF:nordic-qiaa-config1:p1','text':'C3 0.8pF L1 4.7nH RF C4 0.5pF'},
            {'evidence_id':'REF:johanson-2450at18:p1','text':'P/N 2450AT18A0100001E; Passband Frequency (MHz) 2400 - 2500'},
            {'evidence_id':'REF:johanson-2450at18:p2','text':'Pin Number Function 1 Feed 2 NC'}]
        candidate={'footprint':'RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz',
            'manufacturer':'Johanson Technology','manufacturer_part':'2450AT18A0100001E','band_mhz':'2400-2500',
            'pad_roles':{'1':'RF feed','2':'NC; solder for mechanical anchoring, leave electrically unconnected'},
            'datasheet_evidence_id':'REF:johanson-2450at18:p2',
            'datasheet_url':'https://www.johansontechnology.com/docs/3827/Antenna-2450AT18A0100001E-Rev4.0.pdf'}
        packet={'rf_reference_comparison':{'matches_reference':True},'datasheet_excerpts':sources,
            'rf_ground_candidates':['GND'],'relevant_signal_paths':[{'kind':'rf','nets':[{'net':'ANT_RF'}]}],
            'reference_antenna_footprints':[candidate]}
        result=reference_candidate(packet)
        validate(result,sources)
        self.assertEqual(result['reference_antenna_footprints'],[candidate])
        self.assertIn('2450AT18A0100001E',result['approach'])

    def test_nordic_network_comparison_requires_correct_package_values_and_nets(self):
        from types import SimpleNamespace
        def comp(ref,value,fp,pads):
            return SimpleNamespace(pcb=[SimpleNamespace(value=value,footprint=fp,pads=[SimpleNamespace(number=n,net=net) for n,net in pads.items()])])
        project=project_fixture(components={
            'U13':comp('U13','nRF52840','Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm',{'H23':'ANT'}),
            'C34':comp('C34','0.8pF','Capacitor_SMD:C_0402_1005Metric',{'1':'ANT','2':'GND'}),
            'L1':comp('L1','4.7nH','Inductor_SMD:L_0402_1005Metric',{'1':'RFOUT','2':'ANT'}),
            'C33':comp('C33','0.5pF','Capacitor_SMD:C_0402_1005Metric',{'1':'RFOUT','2':'GND'})})
        path={'anchor':'U13','nets':[{'net':'ANT'},{'net':'RFOUT'}]}
        result=compare_nordic_rf_network(project,path)
        self.assertTrue(result['matches_reference'])
        # Reference designators, pad order and ground labels are not part identity.
        import copy
        changed=copy.deepcopy(project)
        mapping={'U13':'U99','C34':'C420','L1':'L15','C33':'C19'}
        changed.components={mapping[ref]:part for ref,part in changed.components.items()}
        for part in changed.components.values():
            for pad in part.pcb[0].pads:
                if pad.net=='GND':pad.net='/radio/VSS'
                if part.pcb[0].value!='nRF52840':pad.number='2' if pad.number=='1' else '1'
        renamed_path={**path,'anchor':'U99'}
        renamed=compare_nordic_rf_network(changed,renamed_path)
        self.assertTrue(renamed['matches_reference'])
        self.assertEqual(renamed['roles']['radio_side_shunt'],'C420')
        changed.components['C99']=comp('C99','1pF','Capacitor_SMD:C_0402_1005Metric',{'1':'ANT','2':'/radio/VSS'})
        self.assertFalse(compare_nordic_rf_network(changed,renamed_path)['matches_reference'])
        del changed.components['C99']
        changed.components['U99'].pcb[0].pads[0].net='OTHER'
        self.assertFalse(compare_nordic_rf_network(changed,renamed_path)['matches_reference'])
        project.components['C33'].pcb[0].value='1pF'
        self.assertFalse(compare_nordic_rf_network(project,path)['matches_reference'])

    def test_rf_guard_does_not_mistake_net_name_substrings_for_connections(self):
        from pcb_checker.repair_design import _validate_rf_proposal
        path={'kind':'rf','nets':[{'net':'ANT'},{'net':'RF_FEED'}]}
        proposal={'antenna_feed_net':'RF_FEED','antenna_ground_net':'GND',
            'changes':['Keep C33, C34 and L1 unchanged.'],
            'diagnosis':'The saved scan did not identify an endpoint; absence is not established.',
            'questions':[]}
        _validate_rf_proposal(proposal,{'relevant_signal_paths':[path],
            'rf_ground_candidates':['GND']})
        proposal['changes']=['Connect the radiator to ANT.']
        with self.assertRaisesRegex(ValueError,'bypass'):
            _validate_rf_proposal(proposal,{'relevant_signal_paths':[path],
                'rf_ground_candidates':['GND']})

    def test_repair_runs_one_ai_assessment_and_checks_its_output_locally(self):
        from pcb_checker.repair_design import investigate, REPAIR_SCHEMA_SMALL
        project=project_fixture(components={},datasheets={'parts':[]},signal_paths={},manufacturing={})
        draft={'title':'Repair','diagnosis':'Unsupported draft claim',
               'changes':[{'op':'set_value','target':'C1','value':'100nF','why':'Verified decoupling.'}],
               'assumptions':[],'questions':[],
               'validation':['Run DRC'],'limitations':['Bias loss not verified']}
        with tempfile.TemporaryDirectory() as temp, \
             patch('pcb_checker.step_help._packet',return_value={'datasheet_excerpts':[]}), \
             patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
             patch('pcb_checker.repair_design.request_design',return_value=draft) as request:
            result=investigate(project,{'id':'R1'},'Repair this',{},Path(temp),'test','test')
        self.assertEqual(request.call_count,1)
        self.assertNotIn('draft_to_check',request.call_args.args[0])
        self.assertIs(request.call_args.kwargs['schema'],REPAIR_SCHEMA_SMALL)
        self.assertEqual(result['diagnosis'],draft['diagnosis'])
        self.assertTrue(result['edit_constraints']['preserve_component_values'])

    def test_truncated_non_rf_repair_retries_narrower_and_succeeds(self):
        from pcb_checker.ai.reviewer import AIError
        from pcb_checker.repair_design import investigate, REPAIR_SCHEMA_SMALL
        proposal={'title':'Fit a verified 100 nF decoupling capacitor at C105',
                  'diagnosis':'C105 is marked 100 uF in a 0402 package, which cannot hold that value; the rail it bypasses has no verified capacitance.',
                  'changes':[{'op':'set_value','target':'C105','value':'100nF','why':'Verified standard decoupling value for this rail.'}],
                  'assumptions':[],'questions':[],
                  'validation':['Re-run the checks; this finding must disappear.'],
                  'limitations':['Effective capacitance under bias was not verified from a datasheet curve.']}
        calls=[]
        def fake_post_ai(payload, key, **kwargs):
            calls.append(payload)
            if len(calls)==1:
                raise AIError('Meta stopped early: the answer exceeded the output limit. Narrow the request scope and run again.',
                              diagnostics={'error_kind':'truncated','retryable':False})
            return {'status':'completed','steps':[{'type':'model_output','content':[{'type':'text','text':json.dumps(proposal)}]}]}
        with tempfile.TemporaryDirectory() as temp:
            from types import SimpleNamespace
            project=project_fixture(components={'C105':SimpleNamespace(pcb=[],schematic=[],schematic_pins=[])},
                datasheets={'parts':[{'reference':'C105','pdf_status':'read'}],'documents':{}},
                signal_paths={'paths':[],'board_measurements':{'stackup':{}}},
                manufacturing={},design_context={},provenance={})
            packet={'datasheet_excerpts':[{'evidence_id':'D:C105:%d'%i,'text':'x'*9000} for i in range(10)],
                    'relevant_signal_paths':[],'components':[],
                    'placements':[{'reference':'R%d'%i} for i in range(200)],
                    'routing_chains':{'chains':[]}}
            with patch('pcb_checker.step_help._packet',return_value=packet), \
                 patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
                 patch('pcb_checker.repair_design.post_ai',side_effect=fake_post_ai):
                result=investigate(project,{'id':'R5','components':['C105']},
                    'Fix C105',{},Path(temp),'key','meta/muse-spark-1.3-contributor')
        self.assertEqual(len(calls),2)
        first_len=len(json.dumps(calls[0]['input']))
        second_len=len(json.dumps(calls[1]['input']))
        self.assertLess(second_len,first_len)
        second_schema=calls[1]['response_format']['schema']
        self.assertNotIn('requirements',second_schema['properties'])
        self.assertNotIn('antenna_feed_net',second_schema['properties'])
        self.assertEqual(result['title'],proposal['title'])
        self.assertEqual(result['changes'],proposal['changes'])

    def test_repair_context_always_carries_current_intent_manufacturer_stackup_and_answers(self):
        from pcb_checker.repair_design import capture_repair_context
        project=project_fixture(
            design_context={'description':'Battery-powered sensor','operating_context':{'radio':'BLE'},
                            'part_choices':{'U1':{'code':'C12345'}}},
            manufacturing={'selection':{'manufacturer':'ExampleFab'}},
            checks_performed=['erc','drc'],connectivity={'status':'compared'},engine={'name':'KiCad'},
            review={'limitations':['RF not simulated']},selected_files={'pcb':'board.kicad_pcb'},
            provenance={'source_hashes':{'board.kicad_pcb':'a'*64}})
        packet={'intent_note':'Radio edge needs clearance.','manufacturer_check':{'actual':'0.12 mm'},
                'saved_stackup':{'layers':4},'board_measurements':{'thickness_mm':1.2}}
        result=capture_repair_context(packet,project,{'id':'R2','key':'group-2','title':'Antenna feed','components':['U1']},
                                      {'antenna':'printed antenna'})
        context=result['repair_context']
        self.assertEqual(context['intent_for_finding'],'Radio edge needs clearance.')
        self.assertEqual(context['operating_context']['radio'],'BLE')
        self.assertEqual(context['manufacturer_selection']['manufacturer'],'ExampleFab')
        self.assertEqual(context['board_stackup']['layers'],4)
        self.assertEqual(context['design_answers']['antenna'],'printed antenna')
        self.assertEqual(context['part_choices']['U1']['code'],'C12345')
        self.assertEqual(context['source_revision']['board.kicad_pcb'],'a'*64)

    def test_project_name_does_not_become_user_intent(self):
        from pcb_checker.repair_design import capture_repair_context
        from pcb_checker.step_help import _packet
        for name in ('smartwatch','watchdog_controller','ddr5_memory'):
            project=project_fixture(provenance={'project_name':name})
            group={'id':'R1','key':'test','components':[]}
            repair=capture_repair_context({},project,group,{})
            step=_packet(project,group,{'steps':['Review this connection']},0,'Explain',[])
            for description in (repair['repair_context']['device_description'],step['project_context']['device_description']):
                self.assertNotIn('User identifies',description)
                self.assertNotIn('smartwatch/wearable',description)

    def test_answered_question_is_removed_and_substantive_answer_stays_visible(self):
        from pcb_checker.repair_design import remove_answered_questions,constrain_unresolved_battery_context
        result={'questions':[{'id':'external_enclosure_metal','question':'Any nearby metal?','why_needed':'RF'}],
                'assumptions':[]}
        removed=remove_answered_questions(result,{'external_enclosure_metal':'No nearby metal; research a good battery and its orientation.'})
        self.assertEqual(len(removed),1)
        self.assertEqual(result['questions'],[])
        self.assertIn('battery and its orientation',result['assumptions'][0])
        result={'questions':[{'id':'antenna_environment','question':'What is the environment?','why_needed':'RF'}],
                'assumptions':[]}
        removed=remove_answered_questions(result,{'external_enclosure_metal':'No nearby metal; battery and orientation still need research.'})
        self.assertEqual(len(removed),1)
        self.assertEqual(result['questions'],[])
        self.assertIn('battery and orientation',result['assumptions'][0])
        proposal={'assumptions':['Battery orientation is assumed based on standard wearable requirements.',
                                 'RF path topology matches the reference.'],'limitations':[]}
        constrain_unresolved_battery_context(proposal,{'external_enclosure_metal':
            'No nearby metal; find a good battery and determine orientation from its pins.'})
        self.assertEqual(proposal['assumptions'],['RF path topology matches the reference.',
            'User reports no nearby metal or shielding is planned around the antenna.'])
        self.assertIn('No battery has been selected.',proposal['limitations'][0])
        proposal={'assumptions':['The enclosure is non-metallic based on user input.',
                                 'No metal enclosure is present.'],'limitations':[]}
        constrain_unresolved_battery_context(proposal,{'external_enclosure_metal':'No nearby metal.'})
        self.assertEqual(proposal['assumptions'],['User reports no nearby metal or shielding is planned around the antenna.'])
        self.assertIn('finished enclosure material and geometry',proposal['limitations'][0])

    def test_ai_assessment_keeps_valid_analysis_when_quote_is_replaced_with_verified_source_quote(self):
        from pcb_checker.repair_design import investigate
        path={'id':'rf-U13','kind':'rf','nets':[{'net':'ANT'}, {'net':'MATCHED_FEED'}]}
        project=project_fixture(components={'U13':SimpleNamespace(pcb=[])},
            datasheets={'parts':[{'reference':'U13','pdf_status':'read'}],'documents':{}},
            signal_paths={'paths':[path],'board_measurements':{'stackup':{}}},manufacturing={},design_context={},provenance={})
        quote='C3 0.8pF L1 4.7nH RF C4 0.5pF'
        document={'url':'https://nsscprodmedia.blob.core.windows.net/reference.zip','title':'Nordic QIAA config1','sha256':'test',
                  'pages':[{'page':1,'text':quote}]}
        proposal={'title':'Reference comparison','diagnosis':'The scan does not establish an open circuit.',
            'approach':'Keep the saved values. Inspect the endpoint before changing the board.',
            'antenna_feed_net':'MATCHED_FEED','antenna_ground_net':'',
            'changes':['Keep C33, C34 and L1 unchanged.'], 'assumptions':[], 'questions':[],
            'requirements':[{'requirement':'unsupported','evidence_id':'wrong-part','quote':'Another component says so.'}],
            'validation':['Inspect endpoint'],'limitations':['RF performance remains unverified']}
        bad=dict(proposal,diagnosis='The antenna is missing and the RF path is open.')
        corrected=dict(proposal,requirements=[{'requirement':'The reference matching network ends at an RF output.',
            'evidence_id':'REF:nordic-qiaa-config1:p1','quote':quote}])
        packet={'datasheet_excerpts':[],'relevant_signal_paths':[path],'components':[]}
        with tempfile.TemporaryDirectory() as temp, \
             patch('pcb_checker.step_help._packet',return_value=packet), \
             patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
             patch('pcb_checker.repair_design.reference_document',return_value=document), \
             patch('pcb_checker.repair_design.compare_nordic_rf_network',return_value={'matches_reference':True,'output_net':'MATCHED_FEED'}), \
             patch('pcb_checker.repair_design.request_design',side_effect=[bad,corrected]) as request:
            result=investigate(project,{'id':'R1','components':['U13'],'signal_path_id':'rf-U13'},
                'Review the RF chain',{},Path(temp),'key','gemini')
        self.assertEqual(request.call_count,2)
        self.assertEqual(result['title'],'Reference comparison',result.get('service_message'))
        self.assertEqual(result['diagnosis'],corrected['diagnosis'])
        self.assertEqual(result['requirements'][0]['quote'],quote)
        self.assertIn('corrected a source citation',result['service_message'])
        _validate = __import__('pcb_checker.repair_design',fromlist=['_validate_rf_proposal'])._validate_rf_proposal
        _validate(result,{**packet,'rf_ground_candidates':[],'reference_antenna_footprints':[]})

    def test_unsafe_rf_recommendation_is_corrected_without_discarding_checked_ai_context(self):
        from pcb_checker.repair_design import investigate
        path={'id':'rf-U13','kind':'rf','nets':[{'net':'ANT'}, {'net':'MATCHED_FEED'}]}
        project=project_fixture(components={'U13':SimpleNamespace(pcb=[])},
            datasheets={'parts':[{'reference':'U13','pdf_status':'read'}],'documents':{}},
            signal_paths={'paths':[path],'board_measurements':{'stackup':{}}},manufacturing={},design_context={},provenance={})
        quote='C3 0.8pF L1 4.7nH RF C4 0.5pF'
        document={'url':'https://nsscprodmedia.blob.core.windows.net/reference.zip','title':'Nordic QIAA config1','sha256':'test',
                  'pages':[{'page':1,'text':quote}]}
        unsafe={'title':'AI RF path review','diagnosis':'The antenna is missing because the endpoint was not identified.',
            'approach':'Keep the saved matching parts and confirm the antenna implementation at the RF output.',
            'antenna_feed_net':'MATCHED_FEED','antenna_ground_net':'',
            'changes':['Keep C33, C34 and L1 unchanged. Connect the antenna to ANT.'],
            'assumptions':[],'questions':[{'id':'board_space','question':'What clearance is available around the board antenna?',
                'why_needed':'The board must have enough space.'}], 'requirements':[{'requirement':'Use the package-specific network.',
                'evidence_id':'REF:nordic-qiaa-config1:p1','quote':quote}],
            'validation':['Trace the output through copper and connector pads.'],'limitations':['RF performance requires measurement.']}
        packet={'datasheet_excerpts':[],'relevant_signal_paths':[path],'components':[]}
        with tempfile.TemporaryDirectory() as temp, \
             patch('pcb_checker.step_help._packet',return_value=packet), \
             patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
             patch('pcb_checker.repair_design.reference_document',return_value=document), \
             patch('pcb_checker.repair_design.compare_nordic_rf_network',return_value={'matches_reference':True,'output_net':'MATCHED_FEED'}), \
             patch('pcb_checker.repair_design.request_design',side_effect=[unsafe,unsafe,unsafe]):
            result=investigate(project,{'id':'R1','components':['U13'],'signal_path_id':'rf-U13'},
                'Review the RF chain',{},Path(temp),'key','gemini')
        self.assertEqual(result['ai_assessment_status'],'available',result.get('service_message'))
        self.assertIn('Nordic matching network',result['title'])
        self.assertIn('not proof the antenna is absent',result['diagnosis'])
        self.assertIn('AI assessment is available',result['service_message'])
        self.assertIn('does not itself specify an antenna',result['approach'])
        self.assertIn('RF performance requires measurement.',result['limitations'])

    def test_ai_question_about_saved_geometry_is_replaced_with_external_context_question(self):
        from pcb_checker.repair_design import investigate
        path={'id':'rf-U13','kind':'rf','nets':[{'net':'ANT'}, {'net':'MATCHED_FEED'}]}
        project=project_fixture(components={'U13':SimpleNamespace(pcb=[])},
            datasheets={'parts':[{'reference':'U13','pdf_status':'read'}],'documents':{}},
            signal_paths={'paths':[path],'board_measurements':{'stackup':{}}},manufacturing={},design_context={},provenance={})
        quote='C3 0.8pF L1 4.7nH RF C4 0.5pF'
        document={'url':'https://nsscprodmedia.blob.core.windows.net/reference.zip','title':'Nordic QIAA config1','sha256':'test',
                  'pages':[{'page':1,'text':quote}]}
        proposal={'title':'RF review','diagnosis':'The scan did not identify the antenna endpoint; absence is not established.',
            'approach':'Compare the RF output with the Nordic package-specific layout.',
            'antenna_feed_net':'MATCHED_FEED','antenna_ground_net':'',
            'changes':['Preserve C33, C34 and L1 values.'], 'assumptions':[],
            'questions':[{'id':'board_space','question':'What clearance is available around the board antenna?',
                          'why_needed':'The board must have enough space.'}],
            'requirements':[{'requirement':'Use the package-specific network.',
                'evidence_id':'REF:nordic-qiaa-config1:p1','quote':quote}],
            'validation':['Compare the RF output with the reference.'],'limitations':['RF performance still needs measurement.']}
        packet={'datasheet_excerpts':[],'relevant_signal_paths':[path],'components':[]}
        with tempfile.TemporaryDirectory() as temp, \
             patch('pcb_checker.step_help._packet',return_value=packet), \
             patch('pcb_checker.design_review.local_plan',return_value={'actions':[]}), \
             patch('pcb_checker.repair_design.reference_document',return_value=document), \
             patch('pcb_checker.repair_design.compare_nordic_rf_network',return_value={'matches_reference':True,'output_net':'MATCHED_FEED'}), \
             patch('pcb_checker.repair_design.request_design',side_effect=[proposal,proposal,proposal]):
            result=investigate(project,{'id':'R1','components':['U13'],'signal_path_id':'rf-U13'},
                'Review the RF chain',{},Path(temp),'key','gemini')
        self.assertEqual(result['ai_assessment_status'],'available')
        self.assertNotIn('clearance',result['questions'][0]['question'].lower())
        self.assertIn('case material',result['questions'][0]['question'])
        self.assertIn('removed a question about board geometry',result['service_message'])

    def test_reference_citations_must_exist_in_supplied_text(self):
        design={'antenna_feed_net':'','antenna_ground_net':'','title':'Test','diagnosis':'A measured issue','approach':'A proposed repair','changes':['Move R1'],
                'assumptions':[],'questions':[],'requirements':[{'requirement':'Keep copper clear','evidence_id':'ref:1','quote':'No ground plane below the antenna.'}],
                'validation':['DRC'],'limitations':['RF tuning required']}
        sources=[{'evidence_id':'ref:1','text':'No ground plane below the antenna.'}]
        validate(design,sources)
        design['requirements'][0]['quote']='Use this exact 50 ohm geometry.'
        with self.assertRaises(ValueError):validate(design,sources)
        for url in ['http://nsscprodmedia.blob.core.windows.net/x','https://nsscprodmedia.blob.core.windows.net@localhost/x','https://localhost/x','https://nsscprodmedia.blob.core.windows.net:8443/x']:
            with self.assertRaises(ValueError):safe_url(url)

    def test_design_approval_is_bound_to_current_files_and_questions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();source=root/'source';source.mkdir()
            (source/'demo.kicad_sch').write_text(schematic(symbol()))
            (source/'demo.kicad_pcb').write_text(board(footprint()))
            state=AppState(source,root/'reports');self.addCleanup(state.close)
            state.project=analyze_project(source);state.report_id='review'
            state.project.review['groups']=[{'id':'R1','title':'RF endpoint','components':['U13']}]
            state.store.preference('input:review',{'path':str(source)})
            identifier='a'*32;folder=root/'reports'/'.file-fixes'/identifier;folder.mkdir(parents=True)
            record={'id':identifier,'kind':'design_plan','status':'awaiting_design_approval','report_id':'review','group_id':'R1',
                'source':str(source),'edit_request':'Fix the issue','hashes':{p.name:digest(p.read_bytes()) for p in source.iterdir()},
                'design':{'questions':[{'id':'enclosure','question':'Metal nearby?','why_needed':'RF'}]},'design_answers':{}}
            state.file_fixes[identifier]=record
            with self.assertRaisesRegex(AppError,'Answer the design questions'):
                state.continue_file_fix({'fix_id':identifier,'approve_design':identifier})
            with patch.object(state,'prepare_file_fix',return_value={'status':'preparing'}) as prepare:
                state.continue_file_fix({'fix_id':identifier,'answers':{'enclosure':'Plastic'}})
                self.assertTrue(prepare.call_args.args[0]['investigate_design'])
                self.assertNotIn('design_id',prepare.call_args.args[0])
                record['design']['questions']=[]
                state.continue_file_fix({'fix_id':identifier,'approve_design':identifier})
                self.assertEqual(prepare.call_args.args[0]['design_id'],identifier)
                self.assertEqual(record['status'],'design_approved')
            record['status']='awaiting_design_approval'
            with patch.object(state,'prepare_file_fix',side_effect=AppError('Could not start')):
                with self.assertRaises(AppError):state.continue_file_fix({'fix_id':identifier,'approve_design':identifier})
            self.assertEqual(record['status'],'awaiting_design_approval')
            record['status']='awaiting_design_approval';(source/'demo.kicad_pcb').write_text('Changed')
            with self.assertRaisesRegex(ValueError,'changed'):
                state.continue_file_fix({'fix_id':identifier,'approve_design':identifier})

    def test_strip_heavy_context_removes_whole_project_blocks(self):
        from pcb_checker.repair_design import _strip_heavy_context
        packet={'placements':[{'reference':'R12'}],
            'datasheet_excerpts':[{'text':'x'*5000},{'text':'y'*5000},{'text':'z'*5000}],
            'memory_review':{'all':'x'*9999},'relevant_signal_paths':[{'id':'p'}],
            'routing_chains':{'chains':[1,2]},'board_outline':[{'shape':'rect'}],
            'reference_antenna_footprints':[{'footprint':'f'}],'recent_chat':['hi'],
            'erc_findings':['e'],'components':[{'reference':'R12'}],
            'repair_context':{'product_context':{'a':1},'device_description':'d'*3000},
            'finding':{'id':'R1'}}
        stripped=_strip_heavy_context(packet)
        for heavy in ('memory_review','relevant_signal_paths','routing_chains','board_outline',
                      'recent_chat','erc_findings'):
            self.assertNotIn(heavy,stripped)
        self.assertEqual(stripped['reference_antenna_footprints'],[])
        self.assertEqual(stripped['placements'],[])
        self.assertEqual(len(stripped['datasheet_excerpts']),2)
        self.assertLess(len(json.dumps(stripped)),len(json.dumps(packet))//2)
        self.assertEqual(stripped['finding']['id'],'R1')
        self.assertNotIn('product_context',stripped['repair_context'])

    def test_merge_split_proposals_records_overflow_explicitly(self):
        from pcb_checker.repair_design import _merge_split_proposals
        parts=[{'title':'t%d'%i,'diagnosis':'d%d'%i,
                'changes':[{'op':'set_value','target':'R%d'%(i*10+j),'value':'680','why':'w'} for j in range(5)],
                'assumptions':[],'questions':[],'validation':['v'],'limitations':[]}
               for i in range(2)]
        merged=_merge_split_proposals(parts)
        self.assertEqual(len(merged['changes']),8)
        self.assertTrue(any('overflow' in item for item in merged['limitations']),
                        'dropped changes must be disclosed, not silent')
