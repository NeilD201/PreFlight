"""AI-only retries reuse the exact saved checks and cannot trigger extra exports."""
from dataclasses import asdict
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import _pcb_bootstrap  # noqa: F401,E402
from pcb_checker.ai.reviewer import PROVIDERS
from pcb_checker.webapp import AppState, AppError
from pcb_checker.design_review import context_key
from pcb_checker.models import Project
from pcb_checker.report import save_report


class ReviewRetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.state=AppState(None,Path(self.temp.name)/'reports');self.addCleanup(self.state.close)
        self.state.model=PROVIDERS['meta']['model'];self.state.meta_key='fake-key'
        project=Project('example');project.ai_review={'status':'failed','message':'Old failure'}
        project.assembly_export={'status':'unavailable','message':'Saved assembly result','bom':[],'cpl':[],'parts':[],'counts':{}}
        folder=save_report(project,self.state.reports)
        self.state.project=project;self.state.report_id=folder.name
        self.report=folder.name

    def finish(self):
        self.state.worker.join(5)
        self.assertFalse(self.state.worker.is_alive())
        return self.state.snapshot()['job']

    def test_retry_reuses_evidence_and_live_context_without_other_work(self):
        before=asdict(self.state.project)
        context=self.state.store.save_context(context_key(self.state.project),{'description':'Updated device intent'},0)
        reply={'status':'completed','model':PROVIDERS['meta']['model'],'provider':'Meta','result':{'summary':'Reviewed saved evidence','actions':[],'circuit_findings':[],
            'device_description':'Test board','device_evidence_ids':[],'assumptions':[],'questions':[],'limitations':[],'answer':''}}
        with patch('pcb_checker.webapp.run_ai_review',return_value=reply) as review, \
             patch('pcb_checker.webapp.generate_assembly') as assembly, \
             patch('pcb_checker.webapp.analyze_project') as analyze, \
             patch('pcb_checker.webapp.collect_datasheets') as sheets:
            self.state.start('review',{'report_id':self.report,'datasheets':True})
            job=self.finish()
            self.assertEqual(job['status'],'completed',job)
        review.assert_called_once();assembly.assert_not_called();analyze.assert_not_called();sheets.assert_not_called()
        used=review.call_args.args[0]
        self.assertEqual(used.design_context,context)
        self.assertEqual(used.assembly_export,before['assembly_export'])
        self.assertEqual(used.provenance['parent_report'],self.report)
        self.assertNotEqual(self.state.report_id,self.report)
        self.assertEqual(self.state.project.ai_review['status'],'completed')
        self.assertIn('Old failure',(self.state.reports/self.report/'report.json').read_text())

    def test_stale_tab_and_changed_linked_files_rejected_before_provider(self):
        with patch('pcb_checker.webapp.run_ai_review') as review:
            with self.assertRaises(AppError):self.state.start('review',{'report_id':'old'})
            self.state.store.preference('input:'+self.report,{'path':self.temp.name})
            with patch('pcb_checker.webapp.file_fixes.source_status',return_value={'status':'changed'}):
                with self.assertRaises(AppError):self.state.start('review',{'report_id':self.report})
            review.assert_not_called()

    def test_saved_asset_failure_stops_before_spending(self):
        with patch('pcb_checker.webapp.previous_visual',side_effect=OSError('Cannot read saved preview')), \
             patch('pcb_checker.webapp.run_ai_review') as review:
            self.state.start('review',{'report_id':self.report})
            result=self.finish()
        review.assert_not_called();self.assertEqual(result['status'],'failed')
        self.assertEqual(self.state.report_id,self.report)
        self.assertIn('Cannot read saved preview',result['message'])

    def test_provider_failure_is_saved_with_the_actual_cause(self):
        with patch('pcb_checker.webapp.run_ai_review',return_value={'status':'failed','message':'Provider rejected request format'}):
            self.state.start('review',{'report_id':self.report})
            result=self.finish()
        self.assertEqual(result['ai_status'],'failed')
        self.assertIn('Provider rejected request format',result['message'])
        self.assertEqual(self.state.project.assembly_export['message'],'Saved assembly result')


if __name__=='__main__':unittest.main()
