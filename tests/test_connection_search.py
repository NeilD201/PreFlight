import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.models import Project, Pin
from pcb_checker.project_search import connection_neighborhood
from pcb_checker.project_chat import chat_packet
from pcb_checker.chat_context import compact_chat,encode
from test_review import component


class ConnectionSearchTests(unittest.TestCase):
    def board(self):
        p=Project('unflagged')
        p.components={
            'U1':component('U1','MCU',[Pin('1','SDA','bidirectional','SDA'),Pin('2','OUT','output','IN')]),
            'U2':component('U2','Sensor',[Pin('1','SDA','bidirectional','SDA'),Pin('2','OUT','output','OTHER')]),
            'R1':component('R1','4.7k',[Pin('1',net='SDA'),Pin('2',net='+3.3V')]),
            'C1':component('C1','100n',[Pin('1',net='+3.3V'),Pin('2',net='GND')]),
            'R2':component('R2','0',[Pin('1',net='IN'),Pin('2',net='OUT')]),
            'J1':component('J1','Connector',[Pin('1',net='OUT')])}
        return p

    def test_unflagged_part_gets_bus_peers_and_passives_without_inventing_ic_path(self):
        p=self.board();packet=chat_packet(p,'What is connected to U1?',[])
        neighborhood=packet['connection_neighborhood'];nets={n['net']:n for n in neighborhood['nets']}
        self.assertEqual(p.review,{})
        self.assertTrue({'SDA','IN','OUT','+3.3V'}<=nets.keys())
        self.assertEqual({e['reference'] for e in nets['SDA']['endpoints']},{'U1','U2','R1'})
        self.assertNotIn('OTHER',nets,'Do not trace through the sensor internally')
        self.assertNotIn('GND',nets,'Do not recursively expand the entire board')
        self.assertIn('R1',{r['reference'] for r in neighborhood['passive_connections']})

    def test_dnp_is_not_used_as_a_bridge(self):
        p=self.board();p.components['R2'].schematic[0].dnp=True
        self.assertNotIn('OUT',{n['net'] for n in connection_neighborhood(p,['U1'])['nets']})

    def test_bounded_retrieval_reports_omissions(self):
        n=connection_neighborhood(self.board(),['U1'],max_nets=1,max_endpoints=1)
        self.assertEqual(len(n['nets']),1);self.assertGreater(n['omitted_nets'],0)
        self.assertGreater(n['nets'][0]['omitted_endpoints'],0)
        packet=chat_packet(self.board(),'Explain U1',[])
        packet['components']=[{'value':'x'*2000} for _ in range(100)]
        packet['project']['evidence_state']={'status':'changed','warning':'Update review'}
        result,_=compact_chat(packet)
        self.assertEqual(result['project']['evidence_state']['status'],'changed')
        self.assertTrue(result['connection_neighborhood']['nets'])
        self.assertLessEqual(len(encode(result).encode()),48000)
