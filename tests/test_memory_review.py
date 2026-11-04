import random
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402

from pcb_checker.memory_review import review_memory, role, add_findings
from pcb_checker.check_scope import build_scope
from pcb_checker.release_review import before_ordering
from pcb_checker.models import Project, Component, ComponentRecord, Pin
from pcb_checker.review import build_review
from pcb_checker.review_sections import organize_review
from dataclasses import asdict


def component(value, names=(), *, nets=None, dnp=False):
    return {'schematic':[{'value':value,'symbol':value,'dnp':dnp}],
            'schematic_pins':[{'number':str(i+1),'name':name,'net':nets[i] if nets else name,
                               'electrical_type':'bidirectional','no_connect':False} for i,name in enumerate(names)]}


def memory_board(kind='DDR4', width=16):
    names=[f'DQ{i}' for i in range(width)]+['DQS0_t','DQS0_c','CK_t','CK_c']+[f'A{i}' for i in range(8)]+['WE','CS']
    if kind=='SRAM':names=[f'IO{i}' for i in range(width)]+[f'A{i}' for i in range(8)]+['WE','OE','CE']
    if kind=='SDRAM':names=[f'DQ{i}' for i in range(width)]+[f'A{i}' for i in range(8)]+['CLK','WE','RAS','CAS']
    return {'components':{'U1':component(kind,names),'U2':component('Controller',names)},'design_context':{}}


class MemoryReviewTests(unittest.TestCase):
    def test_memory_variants_detected_without_claiming_timing_pass(self):
        for kind,width in [('SRAM',8),('SDRAM',16),('DDR3',8),('DDR4',32),('LPDDR4',16),('DDR5',64)]:
            with self.subTest(kind=kind,width=width):
                data=memory_board(kind,width);r=review_memory(data)
                self.assertEqual(len(r['devices']),2)
                self.assertEqual(r['observations'],[])
                self.assertEqual(r['status'],'needs_review')
                self.assertTrue(all(x['status']=='not_verified' for x in r['requirements']))
                self.assertEqual(next(c for c in before_ordering(data)['checks'] if c['id']=='memory_interface')['status'],'partial')
                self.assertEqual(next(c for c in build_scope(data)['checks'] if c['id']=='memory')['result'],'not_verified')

    def test_other_projects_do_not_inherit_memory_or_watch_rules(self):
        for name,parts in {
            'wireless keyboard':['nRF52840','USB_C_Receptacle','Keyswitch'],
            'drone controller':['STM32F405','DRV8301','BMI270'],
            'audio amplifier':['OPA1656','TPA3255'],
            'power supply':['TPS5430','Inductor'],
            'USB sensor':['ATmega32U4','TMP117'],
            'flash storage':['W25Q128','STM32F4'],
            'smartwatch':['MAX30001','MAX30102','nRF52840'],
        }.items():
            data={'archive':name,'components':{f'U{i}':component(v,['SCK','MISO','MOSI','CS']) for i,v in enumerate(parts)}}
            with self.subTest(project=name):
                result=review_memory(data)
                self.assertEqual(result['devices'],[])
                self.assertEqual(result['status'],'no_candidate_identified')
                self.assertNotIn('memory',{r['id'] for r in build_scope(data)['checks']})

    def test_missing_netlist_and_unknown_symbols_never_pass(self):
        r=review_memory({'components':{'U7':component('DDR4')}})
        self.assertEqual(r['unrecognized_parts'],1)
        self.assertEqual(r['devices'][0]['signals'],[])
        self.assertEqual(r['status'],'needs_review')
        unknown=review_memory({'components':{'U7':component('Custom_BGA')}})
        self.assertEqual(unknown['unrecognized_parts'],1)
        self.assertIn('not a verified absence',unknown['note'])

    def test_renaming_reordering_hierarchy_and_permitted_swaps(self):
        for seed in range(30):
            data=memory_board();rng=random.Random(seed)
            for ref,c in data['components'].items():
                rng.shuffle(c['schematic_pins'])
                for p in c['schematic_pins']:p['net']='/sheet/channel_'+str(seed)+'/'+p['net']
            data['components']={f'U{rng.randrange(100,999)}':data['components']['U1'],'MEM_A':data['components']['U2']}
            # Swapping host data pins does not by itself prove a wiring fault.
            host=data['components']['MEM_A']['schematic_pins']
            dq=[p for p in host if role(p['name'])=='data'];dq[0]['net'],dq[1]['net']=dq[1]['net'],dq[0]['net']
            self.assertEqual(review_memory(data)['observations'],[])

    def test_open_short_and_supply_connection_detected(self):
        data=memory_board();pins=data['components']['U1']['schematic_pins']
        pins[0]['net']='isolated';pins[1]['net']=pins[2]['net'];pins[3]['net']='supply'
        data['components']['U9']=component('Regulator',['VOUT'],nets=['supply'])
        data['components']['U9']['schematic_pins'][0]['electrical_type']='power_out'
        reasons={r['reason'] for r in review_memory(data)['observations']}
        self.assertTrue({'shared_signal_net','supply_connection','no_signal_peer'}<=reasons)

    def test_data_to_clock_connection_is_not_treated_as_a_data_bit_swap(self):
        data=memory_board();pins=data['components']['U2']['schematic_pins']
        data_pin=next(p for p in pins if p['name']=='DQ0');clock=next(p for p in pins if p['name']=='CK_t')
        data_pin['net'],clock['net']=clock['net'],data_pin['net']
        result=review_memory(data)
        self.assertTrue(any(o['reference']=='U1' and o['reason']=='signal_role_mismatch' for o in result['observations']))

    def test_fitted_series_resistors_followed_but_dnp_and_capacitors_are_not(self):
        for kind,dnp,connected in [('R',False,True),('L',False,True),('R',True,False),('C',False,False)]:
            data=memory_board();data['components']['U1']['schematic_pins'][0]['net']='memory_side'
            data['components'][kind+'12']=component('33',['1','2'],nets=['memory_side','DQ0'],dnp=dnp)
            obs=review_memory(data)['observations']
            self.assertEqual(any(o['reference']=='U1' and o['pins']==['1'] for o in obs),not connected)

    def test_explicit_no_connect_is_a_question_not_a_fault(self):
        data=memory_board();data['components']['U1']['schematic_pins'][0]['no_connect']=True
        o=review_memory(data)['observations'][0]
        self.assertEqual(o['reason'],'marked_no_connect');self.assertEqual(o['status'],'needs_review')

    def test_dnp_memory_is_excluded(self):
        data={'components':{'U1':component('DDR4',dnp=True)}}
        self.assertFalse(review_memory(data)['devices'])

    def test_large_interface_has_no_silent_pin_truncation(self):
        data=memory_board(width=256);r=review_memory(data)
        self.assertEqual(r['devices'][0]['role_counts']['data'],256)
        self.assertEqual(r['observations'],[])

    def test_finding_reaches_shared_review_queue_and_rerun_is_idempotent(self):
        data=memory_board();data['components']['U1']['schematic_pins'][0]['net']='isolated'
        p=Project('unrelated-board')
        for ref,c in data['components'].items():
            record=ComponentRecord(ref,c['schematic'][0]['value'],'','test.kicad_sch',1)
            p.components[ref]=Component(ref,[record],[],[Pin(**pin) for pin in c['schematic_pins']])
        add_findings(p);count=len(p.issues);add_findings(p)
        self.assertEqual(len(p.issues),count)
        p.review=build_review(p);sections=organize_review(asdict(p))
        for g in p.review['groups']:
            if g['title'].startswith('Memory signal'):
                self.assertEqual(sections['assignments'][g['id']]['section'],'connections')
                self.assertTrue(sections['assignments'][g['id']]['attention'])
        self.assertTrue(any(g['title'].startswith('Memory signal') for g in p.review['groups']))

    def test_kicad_import_and_netlist_reach_memory_findings(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from xml.etree.ElementTree import Element, SubElement, tostring
        from test_components import schematic, symbol, board, footprint
        from pcb_checker.analysis import analyze_project
        from pcb_checker.native import NativeResult
        data=memory_board(width=8);data['components']['U1']['schematic_pins'][0]['net']='isolated'
        doc=Element('export');components=SubElement(doc,'components');netlist=SubElement(doc,'nets')
        net_ids={p['net']:str(i+1) for i,p in enumerate(data['components']['U1']['schematic_pins']+data['components']['U2']['schematic_pins'])}
        nets={net:SubElement(netlist,'net',name=net) for net in net_ids}
        symbols=[];footprints=[]
        for ref,c in data['components'].items():
            value=c['schematic'][0]['value'];SubElement(components,'comp',ref=ref)
            symbols.append(symbol(ref,value,'Package:BGA'))
            pads=[]
            for pin in c['schematic_pins']:
                SubElement(nets[pin['net']],'node',ref=ref,pin=pin['number'],pinfunction=pin['name'],pintype='bidirectional')
                pads.append(f'(pad "{pin["number"]}" smd rect (net {net_ids[pin["net"]]} "{pin["net"]}"))')
            footprints.append(footprint(ref,value,'Package:BGA')[:-1]+' '.join(pads)+')')
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'controller.kicad_sch').write_text(schematic(*symbols))
            definitions=' '.join(f'(net {number} "{net}")' for net,number in net_ids.items())
            (folder/'controller.kicad_pcb').write_text(board(definitions,*footprints))
            with patch('pcb_checker.analysis.run_native',return_value=NativeResult(netlist=tostring(doc,encoding='unicode'))):
                project=analyze_project(folder,native=True)
        self.assertEqual(project.connectivity['status'],'compared',[(i.code,i.message) for i in project.issues])
        self.assertIn('memory_connection_screening',project.checks_performed)
        self.assertTrue(any(i.code=='MEMORY_CONNECTION_REVIEW' and i.component=='U1' for i in project.issues))
        from pcb_checker.ai.reviewer import evidence_packet
        from pcb_checker.project_chat import chat_packet
        self.assertTrue(evidence_packet(project)['memory_review']['observations'])
        self.assertTrue(chat_packet(project,'Review memory wiring',[])['memory_review']['observations'])

    def test_memory_limits_survive_chat_compaction(self):
        from pcb_checker.chat_context import compact_chat
        review=review_memory(memory_board(width=256))
        packet={'question':'Review memory timing','memory_review':review,'components':[{'text':'x'*60000}]}
        result,stats=compact_chat(packet)
        self.assertTrue(stats['compacted'])
        self.assertEqual(result['memory_review']['limits'],review['limits'])
        self.assertEqual(result['memory_review']['requirements'],review['requirements'])
        self.assertTrue(result['context_limits'])


if __name__=='__main__':unittest.main()
