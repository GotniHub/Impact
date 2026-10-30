import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from impact import db, providers, services, direct_research, costs
from impact.domain import assess
from impact.config import DEFAULT_CRITERIA
from impact import local_quality, ai


class DirectResearchTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_direct_test_')
        self.addCleanup(temp.cleanup)
        data=patch.object(db,'DATA',Path(temp.name)); data.start(); self.addCleanup(data.stop)
        db.init()
        env=patch.dict(os.environ,{'OPENAI_API_KEY':'','GEMINI_API_KEY':'','OLLAMA_API_KEY':'','SERPER_API_KEY':'',
                                 'OLLAMA_BASE_URL':'http://127.0.0.1:11434'})
        env.start(); self.addCleanup(env.stop)
        self.cfg=providers.config('ollama',{'model':'qwen3:4b','web_provider':'direct'})

    def test_local_direct_requires_no_api_key_and_has_zero_api_price(self):
        self.assertTrue(self.cfg['configured'])
        rates=costs.configuration_rates(self.cfg)
        self.assertEqual(costs.estimate(1516,rates)['centrale']['total'],0)
        self.assertFalse(providers.config('gemini',{'web_provider':'direct'})['configured'])

    def test_direct_collection_is_bounded_and_never_calls_search_apis(self):
        home='https://alpha.example/'
        pages={home:{'url':home,'text':'Notre entreprise Alpha. '*800,'links':[
            {'url':'https://alpha.example/a-propos','label':'À propos'},
            {'url':'https://alpha.example/contact','label':'Contact'},
            {'url':'https://private.example/about','label':'À propos'},
            {'url':'https://alpha.example/about?tracking=1','label':'About'}]},
            'https://alpha.example/a-propos':{'url':'https://alpha.example/a-propos','text':'Nous fabriquons des aliments depuis 1980. '*500,'links':[]},
            'https://alpha.example/contact':{'url':'https://alpha.example/contact','text':'Équipe Alpha à Lyon. '*500,'links':[]}}
        def fetch(url): return pages[url],False
        company={'name':'Alpha','facts':{'website':{'value':home}}}
        with patch('impact.direct_research.fetch_page',side_effect=fetch) as requests, \
                patch('impact.providers.json_request') as api:
            docs,warnings,usage=direct_research.collect(company,{},self.cfg)
        self.assertEqual(requests.call_count,3); api.assert_not_called()
        self.assertLessEqual(sum(len(d['content']) for d in docs),6000)
        self.assertEqual(usage['search_calls'],0); self.assertEqual(usage['api_calls'],0)
        self.assertTrue(any('seuls les sites fournis' in w for w in warnings))

    def test_site_quota_is_not_retried_or_replaced_with_a_search_api(self):
        company={'name':'Alpha','facts':{'website':{'value':'https://alpha.example/'}}}
        with patch('impact.direct_research.fetch_page',side_effect=HTTPError('https://alpha.example/',429,'quota',{},None)) as read, \
                patch('impact.providers.json_request') as api:
            with self.assertRaisesRegex(providers.AIError,'aucune page'): direct_research.collect(company,{},self.cfg)
        self.assertEqual(read.call_count,1); api.assert_not_called()

    def test_no_website_and_no_accessible_source_do_not_create_research(self):
        with self.assertRaisesRegex(providers.AIError,'aucun site'):
            direct_research.collect({'name':'Alpha','facts':{}},{},self.cfg)

    def test_robots_disallow_blocks_page_and_redirect_before_fetch(self):
        policy=Mock(); policy.can_fetch.return_value=False
        with patch('impact.direct_research.public_url',side_effect=lambda url:url), \
                patch('impact.direct_research.robots',return_value=policy),patch('impact.direct_research.read') as network:
            with self.assertRaisesRegex(providers.AIError,'robots'): direct_research.fetch_page('https://alpha.example/private')
            with self.assertRaisesRegex(providers.AIError,'robots'):
                direct_research.PageRedirect().redirect_request(None,None,302,'redirect',{},'https://alpha.example/private')
        network.assert_not_called()

    def test_local_direct_uses_small_context_and_rejects_truncated_model_output(self):
        result={'done':True,'done_reason':'stop','message':{'content':'{"ok":true}'}}
        schema={'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok']}
        with patch('impact.providers.json_request',return_value=result) as request:
            output,_=providers.structured('Source officielle.',schema,'Extraire.',self.cfg)
        self.assertEqual(output,{'ok':True})
        options=request.call_args.args[1]['options']
        self.assertEqual(options['num_ctx'],6144); self.assertEqual(options['num_predict'],3200)
        with patch('impact.providers.json_request',return_value={**result,'done_reason':'length'}):
            with self.assertRaisesRegex(providers.AIError,'incomplète'):
                providers.structured('Source officielle.',schema,'Extraire.',self.cfg)

    def test_local_quality_rejects_invented_margin_and_false_international_claim(self):
        url='https://alpha.example/'
        def fact(value,quote,year=2027): return {'value':value,'quote':quote,'source':url,'year':year,'scope':'entite','status':'sourced'}
        result={'facts':{'margin_pct':fact(15,'Nous recherchons la performance opérationnelle.'),
                         'revenue_eur':fact(90_000_000,'90 M€ de chiffre d’affaires'),
                         'finance_year':fact(2027,'Salon en 2027'),
                         'business_model':fact('industriel','Nous concevons des solutions logistiques.'),
                         'international':fact(True,'SIRHA Lyon Janvier 2027')},
                'warnings':[],'contacts':[],'memo':{'pitch':'Présence internationale au salon.'}}
        output=local_quality.apply(result,{'name':'Alpha'},[])
        self.assertNotIn('margin_pct',output['facts'])
        self.assertNotIn('international',output['facts'])
        self.assertNotIn('business_model',output['facts'])
        self.assertNotIn('finance_year',output['facts'])
        self.assertEqual(output['facts']['revenue_eur']['value'],90_000_000)
        self.assertIsNone(output['facts']['revenue_eur']['year'])
        self.assertEqual(output['facts']['revenue_eur']['scope'],'unknown')
        self.assertEqual(output['memo']['generation_mode'],'local_standard')
        self.assertNotIn('internationale',output['memo']['pitch'])

    def test_local_schema_limits_long_text_and_citations_remain_exact(self):
        schema=ai.local_schema()
        self.assertEqual(schema['properties']['facts']['maxItems'],14)
        self.assertEqual(schema['properties']['facts']['items']['properties']['quote']['maxLength'],220)
        self.assertNotIn('pitch',schema['properties'])
        self.assertNotIn('events',schema['properties'])
        with self.assertRaises(providers.AIError):
            providers.validate_schema('x'*221,{'type':'string','maxLength':220})

    def test_finance_scope_must_match_the_accounts_type_in_the_quote(self):
        for scope,quote,expected in [
            ('groupe','Comptes sociaux de 2025 : chiffre d’affaires de 12 millions EUR.','unknown'),
            ('entite','Comptes consolidés de 2025 : chiffre d’affaires de 12 millions EUR.','unknown'),
            ('entite','Comptes sociaux de 2025 : chiffre d’affaires de 12 millions EUR.','entite'),
            ('groupe','Comptes consolidés de 2025 : chiffre d’affaires de 12 millions EUR.','groupe'),
            ('entite','Comptes sociaux et comptes consolidés 2025 : chiffre d’affaires de 12 millions EUR.','unknown'),
        ]:
            with self.subTest(scope=scope,quote=quote):
                fact={'value':12_000_000,'quote':quote,'source':'https://alpha.example/comptes',
                      'year':2025,'scope':scope,'status':'sourced'}
                result={'facts':{'revenue_eur':fact,'finance_scope':{**fact,'value':scope}},
                        'warnings':[],'contacts':[],'memo':{}}
                output=local_quality.apply(result,{'name':'Alpha'},[])
                self.assertEqual(output['facts']['revenue_eur']['scope'],expected)
                if expected=='unknown': self.assertNotIn('finance_scope',output['facts'])

    def test_finance_scope_evidence_from_another_source_does_not_qualify_a_number(self):
        fact={'value':12_000_000,'quote':'Chiffre d’affaires de 12 millions EUR en 2025.',
              'source':'https://alpha.example/chiffres','year':2025,'scope':'entite','status':'sourced'}
        result={'facts':{'revenue_eur':fact,'finance_scope':{**fact,'value':'entite',
                'quote':'Les comptes sociaux concernent uniquement cette société.',
                'source':'https://another.example/comptes'}},'warnings':[],'contacts':[],'memo':{}}
        output=local_quality.apply(result,{'name':'Alpha'},[])
        self.assertEqual(output['facts']['revenue_eur']['scope'],'unknown')

    def test_scope_metadata_cannot_override_a_different_scope_in_the_metric_quote(self):
        fact={'value':12_000_000,'quote':'Comptes consolidés 2025 : chiffre d’affaires de 12 millions EUR.',
              'source':'https://alpha.example/comptes','year':2025,'scope':'entite','status':'sourced'}
        result={'facts':{'revenue_eur':fact,'finance_scope':{**fact,'value':'entite',
                'quote':'Les comptes sociaux sont également disponibles.'}},'warnings':[],'contacts':[],'memo':{}}
        output=local_quality.apply(result,{'name':'Alpha'},[])
        self.assertEqual(output['facts']['revenue_eur']['scope'],'unknown')

    def test_confirmed_empty_crm_applies_only_when_empty_and_does_not_validate_ai_facts(self):
        campaign=services.create_campaign('Salon','2027',[{'name':'Alpha'}],'test')
        company=db.all_rows('companies',campaign['id'])[0]
        default=assess(company,DEFAULT_CRITERIA,[])
        confirmed=assess(company,DEFAULT_CRITERIA,[],crm_empty_confirmed=True)
        self.assertTrue(any('Base clients' in b for b in default['blockers']))
        self.assertFalse(any('Base clients' in b for b in confirmed['blockers']))
        self.assertFalse(confirmed['eligible'])
        crm=[{'id':'client','name':'Alpha','status':'client'}]
        self.assertEqual(assess(company,DEFAULT_CRITERIA,crm,crm_empty_confirmed=True)['exclusion']['state'],'excluded')
        campaign['crm_review']={'empty_confirmed':True,'scope':'test'}; db.put('campaigns',campaign)
        _,rows=services.campaign_results(campaign['id'])
        self.assertFalse(any('Base clients' in b for b in rows[0]['assessment']['blockers']))
        self.assertNotEqual(rows[0]['selection'],'validated')

    def make_job(self):
        campaign=services.create_campaign('Salon','2027',[{'name':'Alpha'}],'test')
        with patch('impact.providers.local_models',return_value=['qwen3:4b']),patch('impact.services.EXECUTOR.submit'):
            return services.start_job(campaign['id'],1,configuration=self.cfg,worker_pid=os.getpid())

    def test_server_restart_keeps_live_external_worker_but_interrupts_dead_worker(self):
        job=self.make_job()
        self.assertTrue(db.external_worker_alive(job))
        with patch('impact.db.external_worker_alive',return_value=True): db.init()
        self.assertEqual(db.get('jobs',job['id'])['status'],'queued')
        with patch('impact.db.external_worker_alive',return_value=False): db.init()
        self.assertEqual(db.get('jobs',job['id'])['status'],'interrupted')

    def test_heartbeat_is_not_overwritten_by_old_job_progress(self):
        job=self.make_job(); old=copy.deepcopy(job)
        job['worker_heartbeat']='2099-01-01T00:00:00+00:00'; db.put('jobs',job)
        old['done']=1; db.put('jobs',old)
        self.assertEqual(db.get('jobs',job['id'])['worker_heartbeat'],'2099-01-01T00:00:00+00:00')

    def test_interrupted_job_does_not_save_an_inflight_result_or_continue(self):
        job=self.make_job()
        def research(*args):
            live=db.get('jobs',job['id']); live.update(status='interrupted',message='Serveur interrompu.'); db.put('jobs',live)
            return {'facts':{},'contacts':[],'triggers':[],'events':[],'memo':{},'warnings':[],'identity_match':True}
        with patch('impact.ai.research',side_effect=research): services.run_job(job['id'])
        live=db.get('jobs',job['id'])
        self.assertEqual(live['status'],'interrupted'); self.assertEqual(live['done'],0)
        self.assertEqual(db.get('companies',job['company_ids'][0])['research_status'],'pending')


if __name__=='__main__': unittest.main(verbosity=2)
