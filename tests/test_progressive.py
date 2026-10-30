import copy
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from impact import ai,db,direct_research,exports,providers,services
from impact.config import DEFAULT_CRITERIA
from impact.domain import make_company,rank,research_key


class ProgressiveTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_progressive_')
        self.addCleanup(temp.cleanup)
        data=patch.object(db,'DATA',Path(temp.name)); data.start(); self.addCleanup(data.stop)
        db.init()
        self.campaign=services.create_campaign('Salon','2027',[
            {'name':'Aliments Alpha','category':'Produits alimentaires','website':'https://alpha.example/'},
            {'name':'Emballages Beta','category':'Emballage'}],'test')
        self.criteria=copy.deepcopy(DEFAULT_CRITERIA)
        self.criteria['select_after_full_research']=True

    def company(self,name='Aliments Alpha',**values):
        return make_company({'name':name,'category':'Produits alimentaires',**values},self.campaign,'test')

    def test_progressive_partial_batch_proposes_without_waiting_or_manual_approval(self):
        good=self.company(identity='true',sector_fit='true',business_model='industriel',
                          revenue_eur='12000000',finance_year=str(datetime.now().year-1),finance_scope='entite')
        good['research_status']='completed'
        for fact in good['facts'].values(): fact['status']='sourced'
        db.put('companies',good)
        c=db.get('campaigns',self.campaign['id']); c['criteria']=self.criteria
        c['crm_review']={'empty_confirmed':True}
        job=db.put('jobs',{'campaign_id':c['id'],'status':'interrupted','done':1,'total':100})
        c['full_research_job_id']=job['id']; db.put('campaigns',c)
        campaign,rows=services.campaign_results(c['id'])
        result=next(r for r in rows if r['id']==good['id'])
        self.assertFalse(campaign['selection_deferred'])
        self.assertEqual(result['selection'],'proposed')
        self.assertFalse(result['validated_assessment']['eligible'])
        self.assertEqual(db.get('companies',good['id'])['facts']['identity']['status'],'sourced')

    def test_shortlist_does_not_claim_missing_finance_or_sector_as_verified(self):
        company=self.company(); original=copy.deepcopy(company)
        result=rank([company],self.criteria,[],crm_empty_confirmed=True)[0]
        self.assertEqual(result['selection'],'shortlisted')
        self.assertFalse(result['assessment']['eligible'])
        self.assertEqual(result['screening']['finance_status'],'à documenter')
        self.assertNotIn('sector_fit',result['facts'])
        self.assertEqual(company,original)
        self.assertTrue(any(title=='Qualification' for title,_ in exports.memo_sections(result)))

    def test_exclusions_negative_sector_and_failed_finance_are_not_shortlisted(self):
        client=self.company(name='Ancien Client')
        wrong=self.company(name='Hors secteur',sector_fit='false')
        small=self.company(name='Petit',revenue_eur='9000000',margin_pct='2',
                           finance_year=str(datetime.now().year-1),finance_scope='entite')
        unknown_margin=self.company(name='Marge inconnue',revenue_eur='9000000',
                           finance_year=str(datetime.now().year-1),finance_scope='entite')
        rows=rank([client,wrong,small,unknown_margin],self.criteria,
                  [{'id':'crm','name':'Ancien Client','status':'client'}])
        self.assertEqual([r['name'] for r in rows if r['selection']=='shortlisted'],['Marge inconnue'])

    def test_priority_scope_only_researches_relevant_unfinished_companies(self):
        _,rows=services.campaign_results(self.campaign['id'])
        candidates=services.research_candidates(rows,priority=True)
        self.assertEqual([r['name'] for r in candidates],['Aliments Alpha'])
        candidates[0]['research_status']='completed'
        self.assertEqual(services.research_candidates(candidates,priority=True),[])
        self.assertEqual(len(services.research_candidates(candidates,True,priority=True)),1)

    def test_shortlist_is_capped_without_filling_with_unrelated_companies(self):
        rows=rank([self.company(name=f'Aliment {i}') for i in range(30)]+
                  [self.company(name='Papier',category='Emballage')],self.criteria,[],crm_empty_confirmed=True)
        self.assertEqual(sum(c['selection']=='shortlisted' for c in rows),20)
        self.assertFalse(any(c['selection']=='validated' for c in rows))
        self.assertNotEqual(next(c for c in rows if c['name']=='Papier')['selection'],'shortlisted')

    def test_kitchen_equipment_and_trade_associations_do_not_inherit_food_sector(self):
        equipment=self.company(name='Couteaux',category='Equipement pour la cuisine',sector_fit='true',business_model='industriel')
        association=self.company(name='Association',activity='Association des fabricants exportateurs',sector_fit='true')
        cold=self.company(name='Froid',category='Equipement de réfrigération',sector_fit='true',business_model='industriel')
        rows=rank([equipment,association,cold],self.criteria,[],crm_empty_confirmed=True)
        self.assertEqual([c['name'] for c in rows if c['selection']=='shortlisted'],['Froid'])

    def test_quick_source_indexes_cannot_invent_sources_or_modify_stands(self):
        schema=ai.quick_schema()
        self.assertNotIn('stand',schema['properties'])
        result=self.empty_quick(); result.update(identity_match=True,identity_evidence=1)
        docs=[{'url':'https://alpha.example/','content':'Aliments Alpha fabrique des produits alimentaires.'}]
        excerpts=ai.quick_evidence(docs)
        self.assertEqual(ai.expand_quick(result,excerpts)['facts'],[])
        result['identity_evidence']=0
        expanded=ai.expand_quick(result,excerpts)
        self.assertEqual(expanded['facts'][0]['source_url'],docs[0]['url'])
        from impact.web_research import enforce_quotes
        self.assertEqual(enforce_quotes(expanded,docs),0)

    def test_pastry_accessories_do_not_become_food_producers_from_exhibitor_category(self):
        accessories=self.company(name='Accessoires',category='Boulangerie, pâtisserie',business_model='industriel',
            activity='Fabrication et traitement de rhodoïde rouleaux, feuilles guitare, bandes en acétate. '
                     'Production de plateaux pour gâteau en carton sur mesure.')
        food=self.company(name='Gâteaux',category='Boulangerie, pâtisserie',business_model='industriel',
                          activity='Fabrication de gâteaux et biscuits, vendus dans des cartons.')
        rows=rank([accessories,food],self.criteria,[],crm_empty_confirmed=True)
        self.assertFalse(next(c for c in rows if c['name']=='Accessoires')['screening']['candidate'])
        self.assertEqual([c['name'] for c in rows if c['selection']=='shortlisted'],['Gâteaux'])

    def test_quick_excerpts_include_late_relevant_information_literally(self):
        text=('Menu cookies navigation accueil. '*250)+' Aliments Alpha : fabrication alimentaire. Chiffre d’affaires 2025 : 12 millions EUR.'
        excerpt=direct_research.relevant_excerpt(text,'Aliments Alpha',2000)
        self.assertIn('12 millions EUR',excerpt)
        self.assertLessEqual(len(excerpt),2000)
        self.assertTrue(all(part in text for part in excerpt.split(' … ')))

    def test_quick_cache_and_output_budget_are_distinct_and_remain_local(self):
        with patch.dict(os.environ,{'OLLAMA_BASE_URL':'http://127.0.0.1:11434'}):
            cfg=providers.config('ollama',{'model':'qwen3:1.7b','web_provider':'direct'})
        quick={**cfg,'research_profile':'quick'}
        company=self.company()
        self.assertNotEqual(research_key(company,self.campaign,self.criteria,cfg['model'],cfg),
                            research_key(company,self.campaign,self.criteria,cfg['model'],quick))
        import json
        with patch('impact.providers.json_request',return_value={'done':True,'done_reason':'stop',
                   'message':{'content':json.dumps(self.empty_quick())}}) as request:
            providers.structured('Source publique.',ai.quick_schema(),'Extraction.',quick)
        self.assertEqual(request.call_args.args[0],'http://127.0.0.1:11434/api/chat')
        self.assertEqual(request.call_args.args[1]['options']['num_predict'],1800)
        self.assertFalse(request.call_args.args[1]['think'])

    @staticmethod
    def empty_quick():
        return {'identity_match':False,'identity_evidence':-1,'sector_fit':{'value':None,'evidence':-1},
                'business_model':{'value':None,'evidence':-1},'activity':{'value':'','evidence':-1},
                'revenue_eur':{'value':None,'evidence':-1,'year':None,'scope':'unknown'},
                'margin_pct':{'value':None,'evidence':-1,'year':None,'scope':'unknown'}}

    def test_quick_schema_rejects_company_name_or_sector_text_in_boolean_fields(self):
        response=self.empty_quick(); response['sector_fit']['value']='agroalimentaire'
        with self.assertRaises(providers.AIError): providers.validate_schema(response,ai.quick_schema())

    def test_quick_refresh_preserves_existing_detailed_memo_signals_and_contacts(self):
        company=db.all_rows('companies',self.campaign['id'])[0]
        company.update(memo={'pitch':'Pitch détaillé existant'},triggers=[{'code':'T1','status':'sourced'}],
                       events=[{'year':2025,'event':'Évolution documentée','status':'sourced'}],
                       contacts=[{'name':'Contact existant','email':'contact@alpha.example','linkedin':'','status':'sourced'}],
                       research_sources=['https://alpha.example/rapport'])
        db.put('companies',company)
        cfg=providers.config('ollama',{'model':'qwen3:1.7b','web_provider':'direct'}); cfg['research_profile']='quick'
        with patch('impact.providers.local_models',return_value=['qwen3:1.7b']),patch('impact.services.EXECUTOR.submit'):
            job=services.start_job(self.campaign['id'],1,True,cfg)
        result={'identity_match':True,'facts':{},'contacts':[],'triggers':[],'events':[],
                'memo':{'pitch':'Support standard'},'warnings':[],'sources':['https://alpha.example/']}
        with patch('impact.ai.research',return_value=result): services.run_job(job['id'])
        after=db.get('companies',company['id'])
        for field in ('memo','contacts','triggers','events'): self.assertEqual(after[field],company[field])
        self.assertIn('https://alpha.example/rapport',after['research_sources'])


if __name__=='__main__': unittest.main(verbosity=2)
