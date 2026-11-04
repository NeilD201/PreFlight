"""Functional paths must respect active-device boundaries and power branches."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.models import Project, Pin, Pad
from pcb_checker.routing_chains import build_routing_chains
from pcb_checker.ai.reviewer import evidence_packet, allowed_evidence
from test_review import component


def circuit():
    p=Project('chain',connectivity={'status':'compared'})
    def add(ref,value,pins):
        p.components[ref]=component(ref,value,[Pin(n,name,typ,net) for n,name,typ,net in pins],[Pad(n,net,1) for n,_,_,net in pins])
    add('J1','Conn_01x02',[('1','Power','passive','RAW'),('2','Return','passive','GND')])
    add('U8','MCP73831-2-MC',[('2','V_{DD}','power_in','RAW'),('3','V_{BAT}','power_out','BAT'),('6','V_{SS}','power_in','GND')])
    add('U4','AP2112K-3.3',[('1','VIN','power_in','BAT'),('5','VOUT','power_out','LOGIC'),('2','GND','power_in','GND')])
    add('U12','AP2112K-1.8',[('1','VIN','power_in','LOGIC'),('5','VOUT','power_out','ANALOG'),('2','GND','power_in','GND')])
    add('U2','MAX30001',[('1','AVDD','power_in','ANALOG'),('2','AGND','power_in','GND')])
    add('C1','1uF',[('1','','passive','LOGIC'),('2','','passive','GND')])
    for ref,c in p.components.items():
        for pin in c.schematic_pins:p.schematic_nets.setdefault(pin.net,[]).append(ref+'.'+pin.number)
    return p,add


class RoutingChainTests(unittest.TestCase):
    def test_cascaded_charger_and_regulators_reach_the_sensor(self):
        p,_=circuit();routing=build_routing_chains(p)
        chain=next(c for c in routing['chains'] if c.get('target_net')=='ANALOG')
        self.assertEqual([s.get('reference') for s in chain['routes'][0]['steps']],[None,'U8','U4','U12'])
        self.assertEqual(chain['loads'][0]['reference'],'U2')
        self.assertTrue(chain['loads'][0]['board_matches'])
        self.assertIn('functional dependency',' '.join(routing['limitations']))

    def test_capacitor_branch_never_becomes_a_dc_supply_route(self):
        p,_=circuit();chain=next(c for c in build_routing_chains(p)['chains'] if c.get('target_net')=='LOGIC')
        self.assertTrue(any(b['reference']=='C1' and b['kind']=='capacitor to return' for b in chain['branches']))
        self.assertFalse(any(s.get('reference')=='C1' for r in chain['routes'] for s in r['steps']))
        self.assertNotIn('GND',chain['nets'])

    def test_an_unknown_ic_does_not_join_its_input_to_its_output(self):
        p,_=circuit();p.components['U4'].schematic[0].value='UNKNOWN';p.components['U4'].pcb[0].value='UNKNOWN'
        chain=next(c for c in build_routing_chains(p)['chains'] if c.get('target_net')=='LOGIC')
        self.assertEqual(chain['routes'][0]['source_status'],'not_identified')
        self.assertEqual(chain['routes'][0]['steps'],[])

    def test_conflicting_part_identity_prevents_converter_inference(self):
        p,_=circuit();p.components['U4'].pcb[0].value='different'
        self.assertNotIn('U4',[s['reference'] for s in build_routing_chains(p)['stages']])

    def test_unpopulated_regulator_is_not_a_power_provider(self):
        p,_=circuit();p.components['U4'].schematic[0].dnp=True
        chain=next(c for c in build_routing_chains(p)['chains'] if c.get('target_net')=='LOGIC')
        self.assertEqual(chain['routes'][0]['source_status'],'not_identified')

    def test_debug_header_exposes_a_rail_without_becoming_its_source(self):
        p,add=circuit()
        add('J5','Conn_01x03',[('1','','passive','LOGIC'),('2','','passive','SWD1'),('3','','passive','SWD2')])
        add('U13','nRF52840',[('1','VDD','power_in','LOGIC'),('2','SWDIO','bidirectional','SWD1'),('3','SWDCLK','input','SWD2')])
        chain=next(c for c in build_routing_chains(p)['chains'] if c.get('target_net')=='LOGIC')
        self.assertEqual(len(chain['routes']),1)
        self.assertEqual(chain['routes'][0]['steps'][0]['pins'][0]['reference'],'J1')

    def test_grounded_shunt_cannot_connect_unrelated_signal_chains(self):
        p,add=circuit()
        add('U3','sensor',[('1','SDA','bidirectional','DATA')]);add('U13','controller',[('1','SDA','bidirectional','DATA')])
        add('C2','10nF',[('1','','passive','DATA'),('2','','passive','GND')])
        signal=next(c for c in build_routing_chains(p)['chains'] if 'DATA' in c['nets'])
        self.assertEqual(signal['nets'],['DATA'])
        self.assertNotIn('U4',[n['reference'] for n in signal['endpoints']])

    def test_signal_coupling_capacitor_preserves_distinct_dc_nets(self):
        p,add=circuit()
        add('U3','sensor',[('1','OUT','output','BEFORE')])
        add('U13','controller',[('1','IN','input','AFTER')])
        add('C2','10nF',[('1','','passive','BEFORE'),('2','','passive','AFTER')])
        chain=next(c for c in build_routing_chains(p)['chains'] if 'BEFORE' in c['nets'])
        self.assertEqual(set(chain['nets']),{'BEFORE','AFTER'})
        self.assertEqual(chain['series'][0]['kind'],'capacitor between nets')
        self.assertEqual({n['reference'] for n in chain['endpoints']},{'U3','U13'})
        self.assertIn('not DC continuity',chain['explanation'])

    def test_power_loop_is_bounded_and_reported(self):
        p,_=circuit();p.components.pop('J1')
        for pin in p.components['U8'].schematic_pins:
            if pin.name=='V_{DD}':pin.net='ANALOG'
        r=build_routing_chains(p)
        chain=next(c for c in r['chains'] if c.get('target_net')=='ANALOG')
        self.assertEqual(chain['routes'][0]['source_status'],'loop')
        self.assertLessEqual(len(chain['routes'][0]['steps']),8)

    def test_input_and_output_on_one_wire_remains_an_explicit_problem(self):
        p,_=circuit()
        for pin in p.components['U4'].schematic_pins:
            if pin.name=='VOUT':pin.net='BAT'
        stage=next(s for s in build_routing_chains(p)['stages'] if s['reference']=='U4')
        self.assertEqual(stage['status'],'input_output_joined')

    def test_joined_converter_input_output_is_flagged_without_guessing_voltages(self):
        from pcb_checker.checks.topology import topology_checks
        p,_=circuit()
        for pin in p.components['U4'].schematic_pins:
            if pin.name=='VOUT':pin.net='BAT'
        findings,checks=topology_checks(p)
        finding=next(f for f in findings if f['rule']=='topology:input_output_joined')
        self.assertEqual(finding['reference'],'U4')
        self.assertEqual({m['pin'] for m in finding['chain_targets']},{'1','5'})
        self.assertIn('bypasses',finding['why'])
        self.assertTrue(any(c['status']=='conflict' for c in checks))

    def test_separate_battery_return_and_ground_are_not_merged_or_flagged(self):
        from pcb_checker.checks.topology import topology_checks
        p,add=circuit()
        add('Q1','FS8205A',[('1','','passive','GND'),('2','','passive','-Bat')])
        self.assertEqual(topology_checks(p)[0],[])

    def test_output_to_return_is_flagged_and_unknown_ic_is_not_guessed(self):
        from pcb_checker.checks.topology import topology_checks
        p,_=circuit()
        for pin in p.components['U4'].schematic_pins:
            if pin.name=='VOUT':pin.net='GND'
        findings,_=topology_checks(p)
        self.assertTrue(any(f['rule']=='topology:output_return_joined' for f in findings))
        p.components['U4'].schematic[0].value=p.components['U4'].pcb[0].value='UNKNOWN'
        self.assertEqual(topology_checks(p)[0],[])

    def test_ai_receives_compact_chain_evidence_and_stable_ids(self):
        p,_=circuit();packet=evidence_packet(p)
        self.assertTrue(packet['routing_chains']['chains'])
        self.assertTrue({c['id'] for c in packet['routing_chains']['chains']}<=allowed_evidence(packet))
        self.assertLess(len(json.dumps(packet['routing_chains'])),len(json.dumps(build_routing_chains(p))))

if __name__=='__main__':unittest.main()
