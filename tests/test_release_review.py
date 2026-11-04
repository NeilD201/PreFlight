"""Manufacturing coverage must not convert unknowns, exceptions or AI prose into passes."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.release_review import before_ordering
from pcb_checker.fabrication import validate_layers, export_fabrication, previous_fabrication
from pcb_checker.models import Project, Pin
from pcb_checker.checks.circuit import build_circuit
from pcb_checker.report import save_report
from test_review import component


def row(data, key):
    return next(r for r in before_ordering(data)['checks'] if r['id']==key)


class ReadinessTests(unittest.TestCase):
    def test_generic_project_default_comes_from_local_saved_selection(self):
        from pcb_checker.__main__ import saved_project_path
        from pcb_checker.storage import WorkspaceStore
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(saved_project_path(td))
            store=WorkspaceStore(td)
            store.preference('linked_kicad_files', {'path':td+'/motor-driver'})
            self.assertEqual(saved_project_path(td),Path(td)/'motor-driver')

    def ready_geometry(self):
        return {'checks_performed':['kicad_drc'], 'board_checks':{'status':'prepared','zone_fill':'refilled',
            'outline_closed':True,'width_mm':10,'height_mm':12,'rule_settings':{'status':'read'}}}

    def test_old_or_failed_refill_cannot_pass_copper_check(self):
        data=self.ready_geometry()
        self.assertEqual(row(data,'copper_connectivity')['status'],'checked')
        data['board_checks']['status']='failed'
        self.assertEqual(row(data,'copper_connectivity')['status'],'missing')
        data.pop('board_checks')
        self.assertEqual(row(data,'copper_connectivity')['status'],'missing')

    def test_real_short_and_ignored_short_rule_both_prevent_pass(self):
        data=self.ready_geometry()
        data['issues']=[{'code':'KICAD_DRC_SHORTING_ITEMS','severity':'error'}]
        self.assertEqual(row(data,'copper_connectivity')['status'],'attention')
        data['issues']=[];data['board_checks']['rule_settings']['ignored_drc']=['shorting_items']
        self.assertEqual(row(data,'copper_connectivity')['status'],'missing')
        self.assertEqual(row(data,'rule_scope')['status'],'partial')

    def test_saved_exclusions_visible_even_when_not_current_violations(self):
        data=self.ready_geometry();data['board_checks']['rule_settings']['drc_exclusions']=2
        self.assertEqual(row(data,'rule_scope')['status'],'attention')
        self.assertIn('2 saved exceptions',row(data,'rule_scope')['detail'])

    def test_successful_ai_does_not_approve_ratings_or_mechanics(self):
        data=self.ready_geometry();data['ai_review']={'status':'completed'}
        for key in ('power','thermal','mechanical','protection','order_preview'):
            self.assertEqual(row(data,key)['status'],'missing',key)

    def test_geometry_failure_or_zero_candidates_cannot_be_a_signal_pass(self):
        data=self.ready_geometry()
        data['board_checks']['signal_geometry']={'status':'unavailable','message':'Could not read geometry'}
        self.assertEqual(row(data,'routing_geometry')['status'],'missing')
        data['board_checks']['signal_geometry']={'status':'measured','straight_tracks':10,'parallel_count':0}
        self.assertEqual(row(data,'routing_geometry')['status'],'partial')

    def test_assigned_candidate_not_treated_as_verified_part(self):
        data={'assembly_export':{'status':'draft','counts':{'fitted':1,'assigned':1,'provisional':1}}}
        self.assertEqual(row(data,'parts')['status'],'partial')
        data['assembly_export']['counts']['unresolved']=1
        self.assertEqual(row(data,'parts')['status'],'attention')

    def test_parity_and_open_outline_are_separate_checks(self):
        data=self.ready_geometry();data['board_checks'].update(parity_requested=True,parity_findings=1,outline_closed=False)
        self.assertEqual(row(data,'parity')['status'],'attention')
        self.assertEqual(row(data,'outline')['status'],'attention')


class ExportIntegrityTests(unittest.TestCase):
    def gerbers(self):
        functions=['Copper,L1,Top','Copper,L2,Bot','Profile,NP','Soldermask,Top','Soldermask,Bot',
                   'Legend,Top','Legend,Bot','Paste,Top','Paste,Bot']
        return {f'custom-name-{i}.gbr':f'%TF.FileFunction,{name}*%\n%FSLAX46Y46*%\nM02*'.encode() for i,name in enumerate(functions)}

    def test_renamed_layers_validated_by_content_not_filename(self):
        validate_layers(self.gerbers(),2)

    def test_job_file_or_duplicate_layer_does_not_fill_missing_copper(self):
        for replacement in (b'{}',b'%TF.FileFunction,Copper,L1,Top*%\n%FSLAX46Y46*%\nM02*'):
            files=self.gerbers();files.pop('custom-name-1.gbr');files['extra.gbr' if replacement!=b'{}' else 'job.gbrjob']=replacement
            with self.assertRaises(ValueError):validate_layers(files,2)

    def test_unclosed_outline_blocks_export_but_keeps_analyzed_copy(self):
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.native._run') as run:
            d=Path(td);p=d/'b.kicad_pcb';p.write_bytes(b'copied board')
            prepared={'status':'prepared','outline_closed':False,'analyzed_pcb_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
            meta,assets=export_fabrication('kicad-cli',p,d,prepared,{})
            self.assertEqual(meta['status'],'blocked');self.assertIn('analyzed-board.kicad_pcb',assets);run.assert_not_called()

    def test_failed_refill_does_not_export_saved_fills_as_refilled(self):
        with tempfile.TemporaryDirectory() as td,patch('pcb_checker.native._run') as run:
            meta,assets=export_fabrication('kicad-cli',Path(td)/'b',Path(td),{'status':'failed'}, {})
            self.assertEqual(meta['status'],'unavailable');self.assertFalse(assets);run.assert_not_called()

    def test_tampered_snapshot_is_not_carried_to_next_report(self):
        with tempfile.TemporaryDirectory() as td:
            d=Path(td);(d/'fabrication-draft.zip').write_bytes(b'changed')
            self.assertEqual(previous_fabrication(d,{'package_sha256':hashlib.sha256(b'original').hexdigest()}),{})

    def test_missing_draft_asset_cannot_publish_download_that_will_404(self):
        p=Project('generic-project');p.fabrication={'status':'draft'}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):save_report(p,td)
            self.assertEqual(list(Path(td).iterdir()),[])


class GeneralBusTests(unittest.TestCase):
    def project(self):
        p=Project('industrial-controller');p.connectivity={'status':'compared'}
        p.components['U1']=component('U1','ARBITRARY_SENSOR',[Pin('1','SDA','bidirectional','unnamed-data'),Pin('2','SCL','input','clock'),Pin('3','VDD','power_in','RAIL')])
        for ref,value,other in [('R1','4.7k','RAIL'),('R2','4.7k','RAIL'),('R3','10k','GND')]:
            p.components[ref]=component(ref,value,[Pin('1',net='unnamed-data'),Pin('2',net=other)])
        p.schematic_nets={'unnamed-data':['U1.1','R1.1','R2.1','R3.1'],'clock':['U1.2']}
        return p

    def test_generic_pin_names_find_bus_and_parallel_pullups(self):
        p=self.project();bus=build_circuit(p)['bus_candidates'][0]
        self.assertEqual({r['reference'] for r in bus['pullups']},{'R1','R2'})
        self.assertAlmostEqual(bus['parallel_pullups'][0]['ohms'],2350)

    def test_dnp_resistor_and_pull_down_are_not_pullups(self):
        p=self.project();p.components['R2'].schematic[0].dnp=True
        bus=build_circuit(p)['bus_candidates'][0]
        self.assertEqual(bus['parallel_pullups'][0]['ohms'],4700)
        del p.components['R1'];self.assertFalse(build_circuit(p)['bus_candidates'][0]['pullups'])

    def test_joined_clock_data_detected_but_alternate_spi_roles_not_condemned(self):
        p=self.project();p.components['U1'].schematic_pins[1].net='unnamed-data'
        self.assertTrue(any(f['rule']=='topology:i2c_joined' for f in build_circuit(p)['part_findings']))
        p.components['U1'].schematic_pins[0].name='SDA/MOSI'
        self.assertFalse(any(f['rule']=='topology:i2c_joined' for f in build_circuit(p)['part_findings']))


if __name__=='__main__':unittest.main()
