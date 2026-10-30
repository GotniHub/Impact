import copy
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from impact import db, providers, services
from impact.config import DEFAULT_CRITERIA
from impact.domain import make_company, rank
from impact.free_batch import FreeBatchRequests, retry_delay


class FreeBatchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_free_batch_')
        self.addCleanup(temp.cleanup)
        self.addCleanup(setattr,db,'DATA',db.DATA)
        db.DATA=Path(temp.name)/'data'; db.init()
        env=patch.dict(os.environ,{'GEMINI_API_KEY':'test-gemini-secret','OLLAMA_API_KEY':'test-web-secret'})
        env.start(); self.addCleanup(env.stop)
        self.cfg=providers.config('gemini',{'model':'gemini-3.5-flash-lite','web_provider':'ollama','gemini_tier':'free'})
        self.out=Path(temp.name)/'output'
        self.campaign=services.create_campaign('Salon','2027',[{'name':'Alpha','city':'Paris'}],'test')
        with patch('impact.services.EXECUTOR.submit'):
            self.job=services.start_job(self.campaign['id'],1,configuration=self.cfg)

    def controller(self):
        controller=FreeBatchRequests(self.cfg,self.out)
        controller.bind(self.job['id'])
        return controller

    def test_429_keeps_retry_metadata_without_exposing_response_body(self):
        cases=[({'Retry-After':'45'}, {'error':'hourly request limit test-web-secret'},45,'hour'),
               ({}, {'error':{'details':[
                   {'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[{'quotaId':'RequestsPerDay'}]},
                   {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':'35s'}]}},35,'day')]
        for headers,body,delay,window in cases:
            exc=HTTPError('https://ollama.com/api/web_search',429,'quota',headers,io.BytesIO(json.dumps(body).encode()))
            with self.subTest(window=window),patch('impact.providers.request.urlopen',side_effect=exc):
                with self.assertRaises(providers.AIError) as raised:
                    providers.json_request('https://ollama.com/api/web_search')
            self.assertEqual(raised.exception.retry_after,delay)
            self.assertEqual(raised.exception.quota_window,window)
            self.assertNotIn('test-web-secret',str(raised.exception))

    def test_retry_respects_header_hourly_and_pacific_daily_reset(self):
        self.assertEqual(retry_delay(providers.AIError('quota',retry_after=45),'ollama.com'),50)
        self.assertEqual(retry_delay(providers.AIError('quota'),'ollama.com'),3605)
        now=datetime(2026,10,20,11,0,tzinfo=timezone.utc)
        delay=retry_delay(providers.AIError('quota',quota_window='day',retry_after=30),
                          'generativelanguage.googleapis.com',now=now)
        self.assertEqual(delay,20*3600+65)

    def test_only_free_gemini_and_ollama_web_are_allowed(self):
        for changes in ({'provider':'openai'},{'gemini_tier':'paid'},
                        {'web_provider':'serper'},{'model':'gemini-pro'}):
            with self.subTest(changes=changes),self.assertRaises(providers.AIError):
                FreeBatchRequests({**self.cfg,**changes},self.out)
        controller=self.controller(); controller.original=Mock()
        for url,payload in [('https://api.openai.com/v1/responses',{}),
                            ('https://google.serper.dev/search',{}),
                            ('https://generativelanguage.googleapis.com/v1beta/interactions',{'model':'gemini-pro'})]:
            with self.subTest(url=url),self.assertRaises(providers.AIError): controller.request(url,payload)
        controller.original.assert_not_called()

    def test_quota_retries_same_request_and_reuses_successful_web_response(self):
        controller=self.controller()
        controller.original=Mock(side_effect=[providers.AIError('quota',http_status=429,retry_after=5),{'results':[]}])
        with patch.object(controller,'wait_quota') as wait:
            first=controller.request('https://ollama.com/api/web_search',{'query':'Alpha'})
            second=controller.request('https://ollama.com/api/web_search',{'query':'Alpha'})
        self.assertEqual(first,second)
        self.assertEqual(controller.original.call_count,2)
        self.assertEqual(controller.original.call_args_list[0],controller.original.call_args_list[1])
        self.assertEqual(controller.cache_hits,1); wait.assert_called_once()
        self.assertNotIn('test-web-secret',(self.out/'requests.jsonl').read_text())

    def test_cancel_during_quota_wait_keeps_company_pending_and_does_not_consume_it(self):
        controller=self.controller()
        def research(*args):
            controller.wait_quota(providers.AIError('quota',http_status=429,retry_after=5),'ollama.com',1)
        with patch('impact.ai.research',side_effect=research),patch('impact.free_batch.time.sleep',
                side_effect=lambda _:services.cancel_job(self.job['id'])):
            services.run_job(self.job['id'])
        job=db.get('jobs',self.job['id'])
        self.assertEqual(job['status'],'cancelled'); self.assertEqual(job['done'],0)
        self.assertEqual(job['errors'],[])
        self.assertEqual(db.all_rows('companies',self.campaign['id'])[0]['research_status'],'pending')

    def test_full_selection_waits_for_job_completion(self):
        campaign=db.get('campaigns',self.campaign['id'])
        campaign['criteria']['progressive_selection']=False
        campaign['full_research_job_id']=self.job['id']; db.put('campaigns',campaign)
        waiting,companies=services.campaign_results(campaign['id'])
        self.assertTrue(waiting['selection_deferred'])
        self.assertTrue(all(c['selection']=='none' for c in companies))
        job=db.get('jobs',self.job['id']); job.update(status='completed',done=job['total']); db.put('jobs',job)
        completed,_=services.campaign_results(campaign['id'])
        self.assertFalse(completed['selection_deferred'])

    def test_complete_batch_does_not_fill_twenty_places_with_incomplete_or_ineligible_companies(self):
        criteria=copy.deepcopy(DEFAULT_CRITERIA); criteria['select_after_full_research']=True
        criteria['progressive_selection']=False
        good=make_company({'name':'Good','city':'Paris','identity':'true','sector_fit':'true',
                          'revenue_eur':'12000000','finance_year':str(datetime.now().year-1),
                          'finance_scope':'entite','business_model':'industriel'},self.campaign,'test')
        good['research_status']='completed'
        for fact in good['facts'].values(): fact['status']='sourced'
        small=copy.deepcopy(good); small.update(id=db.uid(),name='Small')
        small['facts']['revenue_eur']['value']=9_000_000
        pending=copy.deepcopy(good); pending.update(id=db.uid(),name='Pending',research_status='pending')
        ambiguous=copy.deepcopy(good); ambiguous.update(id=db.uid(),name='Ambiguous',research_status='ambiguous')
        ranked=rank([good,small,pending,ambiguous],criteria,[])
        self.assertEqual([c['name'] for c in ranked if c['selection']=='proposed'],['Good'])
        self.assertFalse(any(c['selection']=='validated' for c in ranked))


if __name__=='__main__': unittest.main(verbosity=2)
