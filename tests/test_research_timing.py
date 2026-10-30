import copy
import json
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

from impact import db,services,providers,research_timing as timing
from tools.recover_research_durations import recover


class ResearchTimingTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_timing_')
        self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        data=patch.object(db,'DATA',self.root/'data'); data.start(); self.addCleanup(data.stop)
        db.init()
        self.campaign=services.create_campaign('Salon durées','2027',[{'name':'Alpha'}],'test')

    def job(self,**fields):
        return db.put('jobs',{'campaign_id':self.campaign['id'],'created':'2026-10-20T10:00:00+00:00',
                             'status':'completed','done':1,'total':1,'errors':[],**fields})

    def test_sum_excludes_days_between_jobs_and_marks_missing_history(self):
        jobs=[self.job(elapsed_seconds=3600,timing_complete=True),
              self.job(created='2026-10-29T10:00:00+00:00',elapsed_seconds=1800,timing_complete=True),
              self.job(created='2026-10-29T11:00:00+00:00')]
        summary=timing.summarize(jobs)
        self.assertEqual(summary['seconds'],5400)
        self.assertEqual(timing.format_duration(summary['seconds']),'1 h 30 min')
        self.assertEqual(summary['missing_count'],1); self.assertTrue(summary['partial'])
        self.assertEqual(timing.format_duration(137283.45),'38 h 08 min')
        self.assertEqual(timing.format_duration(None),'Non enregistrée')

    def test_live_elapsed_extends_checkpoint_but_finished_job_stays_fixed(self):
        now=datetime(2026,10,29,10,2,tzinfo=timezone.utc)
        job={'status':'running','elapsed_seconds':60,'timing_updated_at':'2026-10-29T10:00:00+00:00'}
        self.assertEqual(timing.duration(job,now)['seconds'],180)
        self.assertTrue(timing.duration(job,now)['complete'])
        job['status']='interrupted'
        self.assertEqual(timing.duration(job,now)['seconds'],60)
        self.assertFalse(timing.duration(job,now)['complete'])
        for bad in (float('nan'),float('inf'),-1,True,'3600'):
            self.assertFalse(timing.valid_seconds(bad))

    def test_historical_recovery_deduplicates_reports_and_preserves_original_results(self):
        complete=self.job()
        partial=self.job(status='interrupted',worker_heartbeat='2026-10-20T10:05:00+00:00')
        active=self.job(status='running')
        unknown=self.job(status='cancelled')
        before={j['id']:copy.deepcopy(j) for j in (complete,partial,active,unknown)}
        reports=self.root/'reports'; reports.mkdir()
        samples=[{'job':complete,'seconds':120}, {'job':complete,'seconds':120},
                 {'job_id':complete['id'],'seconds':999,'status':'running','done':0},
                 {'job_id':partial['id'],'updated':'2026-10-20T10:05:10+00:00','status':'running','done':1},
                 {'job':active,'seconds':999}, {'job_id':unknown['id'],'seconds':-3}]
        for i,sample in enumerate(samples):
            (reports/f'{i}.json').write_text(json.dumps(sample),encoding='utf-8')
        with patch('impact.providers.json_request') as network:
            changes=recover(self.campaign['id'],reports)
            self.assertEqual(len(changes),2)
            self.assertEqual(db.get('jobs',complete['id'])['elapsed_seconds'],120)
            self.assertTrue(db.get('jobs',complete['id'])['timing_complete'])
            self.assertEqual(db.get('jobs',partial['id'])['elapsed_seconds'],310)
            self.assertFalse(db.get('jobs',partial['id'])['timing_complete'])
            for ident,old in before.items():
                current=db.get('jobs',ident)
                self.assertTrue(all(current[k]==v for k,v in old.items()))
            self.assertEqual(db.get('jobs',active['id']),active)
            self.assertEqual(db.get('jobs',unknown['id']),unknown)
            self.assertEqual(recover(self.campaign['id'],reports),[])
            network.assert_not_called()
        summary=timing.summarize(db.all_rows('jobs',self.campaign['id']),now='2026-10-29T10:00:00+00:00')
        self.assertEqual(summary['seconds'],430)
        self.assertTrue(summary['partial'])

    def test_new_jobs_keep_start_end_and_duration_even_on_failure_or_stop(self):
        empty={'facts':{},'contacts':[],'triggers':[],'events':[],'memo':{},'warnings':[],'identity_match':True}
        cfg=providers.config('ollama',{'model':'qwen3:4b','web_provider':'direct'})
        for result,state in [(empty,'completed'),(providers.AIError('Quota de test'),'failed'),
                             (providers.ResearchCancelled('Arrêt de test'),'cancelled')]:
            with self.subTest(state=state):
                with patch('impact.providers.local_models',return_value=['qwen3:4b']),patch('impact.services.EXECUTOR.submit'):
                    job=services.start_job(self.campaign['id'],1,True,cfg)
                def research(*args):
                    live=db.get('jobs',job['id'])
                    self.assertIn('started_at',live)
                    self.assertFalse(live['timing_complete'])
                    if isinstance(result,Exception): raise result
                    return copy.deepcopy(result)
                with patch('impact.ai.research',side_effect=research): services.run_job(job['id'])
                saved=db.get('jobs',job['id'])
                self.assertEqual(saved['status'],state)
                self.assertTrue(saved['timing_complete'])
                self.assertTrue(timing.valid_seconds(saved['elapsed_seconds']))
                self.assertGreaterEqual(timing.timestamp(saved['finished_at']),timing.timestamp(saved['started_at']))


if __name__=='__main__': unittest.main()
