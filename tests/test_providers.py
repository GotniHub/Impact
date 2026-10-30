import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from impact import providers, ai, db, services, costs
from impact.config import DEFAULT_CRITERIA
from impact.domain import make_company, research_key
from impact import web_research


def empty_result():
    return {'identity_match':True,'identity_explanation':'Entreprise confirmée','facts':[],
            'contacts':[],'triggers':[],'events':[],'pitch':'','pain_points_hypotheses':[],
            'questions':[],'key_message':'','missing_information':[]}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_providers_')
        self.addCleanup(temp.cleanup)
        self.addCleanup(setattr,db,'DATA',db.DATA)
        db.DATA=Path(temp.name); db.init()
        env=patch.dict(os.environ,{'AI_PROVIDER':'openai','OPENAI_API_KEY':'test-openai-secret',
            'OPENAI_MODEL':'gpt-5.5','OLLAMA_MODEL':'qwen3:8b','OLLAMA_API_KEY':'test-web-secret',
            'OLLAMA_BASE_URL':'http://127.0.0.1:11434','GEMINI_API_KEY':'test-gemini-secret',
            'GEMINI_MODEL':'gemini-3.5-flash-lite','GEMINI_TIER':'free','WEB_SEARCH_PROVIDER':'ollama',
            'SERPER_API_KEY':'test-serper-secret'})
        env.start(); self.addCleanup(env.stop)

    def test_configuration_checks_required_keys_and_keeps_secrets_out(self):
        for provider in providers.LABELS:
            cfg=ai.config(provider)
            self.assertTrue(cfg['configured'])
            self.assertNotIn('test-',json.dumps(cfg))
        with patch.dict(os.environ,{'OLLAMA_API_KEY':''}):
            cfg=ai.config('ollama')
            self.assertTrue(cfg['text_configured']); self.assertFalse(cfg['configured'])
            self.assertTrue(ai.config('openai')['configured'])
            self.assertTrue(ai.config('gemini',{'web_provider':'serper'})['configured'])
        self.assertFalse(ai.config('ollama',{'model':'gpt-oss:120b-cloud'})['configured'])
        with self.assertRaises(providers.AIError): providers.local_url('http://example.com:11434')

    def test_ollama_structured_uses_local_schema_and_checks_completion(self):
        schema=ai.obj({'ok':{'type':'boolean'}})
        cfg=ai.config('ollama')
        response={'done':True,'done_reason':'stop','message':{'content':'{"ok":true}'},'prompt_eval_count':12,'eval_count':4}
        with patch('impact.providers.json_request',return_value=response) as call:
            result,usage=ai.structured('Réponds ok.',schema,'check',cfg)
        self.assertEqual(result,{'ok':True})
        self.assertEqual(usage['output_tokens'],4)
        self.assertEqual(call.call_args.args[0],'http://127.0.0.1:11434/api/chat')
        self.assertEqual(call.call_args.args[1]['format'],schema)
        self.assertFalse(call.call_args.args[1]['stream'])
        with patch('impact.providers.json_request',return_value={**response,'done_reason':'length'}):
            with self.assertRaises(providers.AIError): ai.structured('x',schema,'x',cfg)

    def test_gemini_uses_stateless_interactions_and_final_text_only(self):
        cfg=ai.config('gemini'); schema=ai.obj({'ok':{'type':'boolean'}})
        response={'status':'completed','steps':[
            {'type':'thought','content':[{'type':'text','text':'secret thought'}]},
            {'type':'model_output','content':[{'type':'text','text':'{"ok":true}'}]}],
            'usage':{'total_input_tokens':20,'total_output_tokens':4}}
        with patch('impact.providers.json_request',return_value=response) as call:
            result,_=ai.structured('x',schema,'x',cfg)
        self.assertEqual(result,{'ok':True})
        payload=call.call_args.args[1]
        self.assertFalse(payload['store']); self.assertNotIn('tools',payload)
        self.assertEqual(payload['response_format']['schema'],schema)
        self.assertEqual(call.call_args.kwargs['headers']['x-goog-api-key'],'test-gemini-secret')

    def test_invalid_model_json_is_rejected_before_any_save(self):
        schema=ai.obj({'ok':{'type':'boolean'}})
        for text in ['not json','{"ok":1}','{}','{"ok":true,"extra":1}']:
            with self.subTest(text=text),patch('impact.providers.json_request',return_value={'done':True,'message':{'content':text}}):
                with self.assertRaises(providers.AIError): ai.structured('x',schema,'x',ai.config('ollama'))

    def test_non_openai_research_rejects_invented_quotes_and_urls(self):
        campaign=services.create_campaign('Salon','2027',[{'name':'Entreprise Alpha'}],'test')
        company=db.all_rows('companies',campaign['id'])[0]
        url='https://alpha.example/contact'
        documents=[{'url':url,'content':'Notre siège social est situé à Lyon et notre équipe accompagne les clients.'}]
        result=empty_result()
        result['facts']=[{'field':'city','value':'Lyon','source_url':url,'quote':'Notre siège social est situé à Lyon','year':None,'scope':'unknown','edition':''},
                         {'field':'phone','value':'0123456789','source_url':url,'quote':'Téléphone inventé absent de la source','year':None,'scope':'unknown','edition':''}]
        with patch('impact.web_research.collect',return_value=(documents,[],{'search_calls':5})), \
             patch('impact.ai.structured',return_value=(result,{})):
            output=ai.research(company,campaign,DEFAULT_CRITERIA,ai.config('ollama'))
        self.assertIn('city',output['facts']); self.assertNotIn('phone',output['facts'])
        self.assertTrue(any('écarté' in w for w in output['warnings']))

    def test_web_collection_is_bounded_and_quota_does_not_fall_back(self):
        company={'name':'Alpha','facts':{}}; campaign={'name':'Salon','edition':'2027'}
        def respond(url,payload,*args,**kwargs):
            if url.endswith('web_search'):
                return {'results':[{'url':'https://alpha.example','title':'Alpha','content':'Extrait de recherche suffisamment long.'}]}
            return {'content':'Texte de la page officielle suffisamment long.'}
        with patch('impact.web_research.json_request',side_effect=respond) as call:
            docs,_,usage=web_research.collect(company,campaign,ai.config('ollama'))
        self.assertEqual(call.call_count,6); self.assertEqual(usage['search_calls'],5)
        self.assertIn('page officielle',docs[0]['content'])
        with patch('impact.web_research.json_request',side_effect=providers.AIError('Quota atteint')) as call:
            with self.assertRaisesRegex(providers.AIError,'Quota'): web_research.collect(company,campaign,ai.config('gemini'))
        self.assertEqual(call.call_count,1)

    def test_http_429_stops_without_retry_or_secret_disclosure(self):
        error=HTTPError('https://ollama.com/api/web_search',429,'quota',{},None)
        with patch('impact.providers.request.urlopen',side_effect=error) as network,patch('impact.providers.time.sleep') as sleep:
            with self.assertRaisesRegex(providers.AIError,'Quota') as raised:
                providers.json_request('https://ollama.com/api/web_search',{'query':'x'},{'Authorization':'Bearer test-web-secret'})
        self.assertEqual(network.call_count,1); sleep.assert_not_called()
        self.assertNotIn('test-web-secret',str(raised.exception))

    def test_missing_web_page_keeps_other_sources_but_auth_and_quota_stop(self):
        company={'name':'Alpha','facts':{}}; campaign={'name':'Salon','edition':'2027'}
        def respond(url,payload,*args,**kwargs):
            if url.endswith('web_search'):
                return {'results':[
                    {'url':'https://alpha.example/missing','content':'Extrait de recherche suffisamment long.'},
                    {'url':'https://alpha.example/official','content':'Source officielle.'}]}
            if payload['url'].endswith('/missing'):
                raise providers.AIError('Lecture web Ollama : Page source introuvable.',http_status=404)
            return {'content':'Texte de la page officielle suffisamment long.'}
        with patch('impact.web_research.json_request',side_effect=respond):
            docs,warnings,_=web_research.collect(company,campaign,ai.config('ollama'))
        self.assertEqual(len(docs),2)
        self.assertIn('page officielle',docs[1]['content'])
        self.assertEqual(len(warnings),1)
        for status in (401,403,429):
            def blocked(url,*args,**kwargs):
                if url.endswith('web_search'):
                    return {'results':[{'url':'https://alpha.example','content':'Extrait disponible.'}]}
                raise providers.AIError('Accès au service refusé.',http_status=status)
            with self.subTest(status=status),patch('impact.web_research.json_request',side_effect=blocked):
                with self.assertRaises(providers.AIError):
                    web_research.collect(company,campaign,ai.config('ollama'))

    def test_marketplace_profile_is_not_saved_as_official_company_website(self):
        for url,rejected in [('https://shop.app/m/alpha',True),
                             ('https://fr.linkedin.com/company/alpha',True),
                             ('https://alpha.example/',False)]:
            result=empty_result()
            result['facts']=[{'field':'website','value':url,'source_url':url,
                              'quote':'Entreprise Alpha : notre boutique en ligne.'}]
            with self.subTest(url=url):
                filtered=ai.filter_result(result,{url},{'edition':'2027'},DEFAULT_CRITERIA)
                self.assertEqual('website' not in filtered['facts'],rejected)
                if rejected: self.assertTrue(any('Site officiel écarté' in w for w in filtered['warnings']))

    def test_http_404_distinguishes_source_page_from_model(self):
        for service,expected in [('Lecture web Ollama','Page source'),('Ollama local','Accès au modèle')]:
            with self.subTest(service=service),patch('impact.providers.request.urlopen',
                    side_effect=HTTPError('https://example.com',404,'missing',{},None)):
                with self.assertRaisesRegex(providers.AIError,expected) as raised:
                    providers.json_request('https://example.com',service=service)
            self.assertEqual(raised.exception.http_status,404)

    def test_private_sources_and_redirects_are_refused(self):
        for ip in ['127.0.0.1','10.0.0.1','169.254.169.254','::1']:
            with self.subTest(ip=ip),patch('impact.web_research.socket.getaddrinfo',return_value=[(2,1,6,'',(ip,80))]):
                with self.assertRaises(providers.AIError): web_research.public_url('http://untrusted.example')

    def test_provider_prices_never_claim_an_unlimited_free_total(self):
        rates=costs.configuration_rates(ai.config('ollama'))
        self.assertEqual(rates['input_per_million'],0); self.assertIsNone(rates['search_per_thousand'])
        rates=costs.configuration_rates(ai.config('gemini',{'web_provider':'serper','gemini_tier':'paid'}))
        self.assertEqual(costs.estimate(4000,rates)['centrale']['total'],124)
        rates=costs.configuration_rates(ai.config('ollama',{'web_provider':'serper'}))
        self.assertEqual(costs.estimate(1,rates)['centrale']['web'],50)
        self.assertEqual(costs.estimate(0,rates)['centrale']['web'],0)

    def test_cache_is_separated_and_running_job_keeps_its_provider(self):
        campaign=services.create_campaign('Salon','2027',[{'name':'Alpha'}],'test')
        company=db.all_rows('companies',campaign['id'])[0]
        cfg=ai.config('openai')
        key=research_key(company,campaign,DEFAULT_CRITERIA,'same-model',cfg)
        self.assertNotEqual(key,research_key(company,campaign,DEFAULT_CRITERIA,'same-model',ai.config('ollama')))
        with patch('impact.services.EXECUTOR.submit'):
            job=services.start_job(campaign['id'],1,configuration=cfg)
        cfg['provider']='gemini'  # La sélection de l'appelant ne doit pas modifier le lot enregistré.
        result={'facts':{},'contacts':[],'triggers':[],'events':[],'memo':{},'warnings':[],'identity_match':True,'sources':[],'usage':{}}
        with patch.dict(os.environ,{'AI_PROVIDER':'gemini'}),patch('impact.ai.research',return_value=result) as research:
            services.run_job(job['id'])
        self.assertEqual(research.call_args.args[3]['provider'],'openai')
        self.assertEqual(db.get('companies',company['id'])['research_provider'],'openai')
        self.assertNotIn('test-openai-secret',json.dumps(db.get('jobs',job['id'])))

    def test_missing_local_model_blocks_job_before_web_consumption(self):
        campaign=services.create_campaign('Salon','2027',[{'name':'Alpha'}],'test')
        with patch('impact.providers.local_models',return_value=[]),patch('impact.web_research.collect') as web:
            with self.assertRaises(providers.AIError): services.start_job(campaign['id'],1,configuration=ai.config('ollama'))
        web.assert_not_called(); self.assertEqual(db.all_rows('jobs'),[])


if __name__=='__main__': unittest.main(verbosity=2)
