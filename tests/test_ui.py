import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from streamlit.testing.v1 import AppTest

def navigate(app,page):
    return next(button for button in app.sidebar.button if button.label==page).click().run()


class StreamlitTests(unittest.TestCase):
    def setUp(self):
        from impact import db
        import streamlit as st
        temp=tempfile.TemporaryDirectory(prefix='impact_ui_test_')
        self.addCleanup(temp.cleanup)
        data=patch.object(db,'DATA',Path(temp.name))
        data.start(); self.addCleanup(data.stop)
        env=patch.dict(os.environ,{'IMPACT_DATA_DIR':temp.name,'OPENAI_API_KEY':''})
        env.start(); self.addCleanup(env.stop)
        st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)
        # Les tests métier restent isolés ; test_auth_ui couvre les vrais parcours d'accès.
        for name,kwargs in [('require_login',{'return_value':'test@example.org'}),('guard_session',{'return_value':None})]:
            mocked=patch('impact.auth_ui.'+name,**kwargs); mocked.start(); self.addCleanup(mocked.stop)

    def test_selection_replacement_and_stale_exports_require_no_new_research(self):
        from impact import db,services
        db.init()
        campaign=services.create_campaign('Salon remplacement','2027',[
            {'name':f'Aliment {i}','category':'Produits alimentaires'} for i in range(7)],'test')
        campaign['criteria'].update(target_count=3,backup_count=3)
        campaign['crm_review']={'empty_confirmed':True}; db.put('campaigns',campaign)
        for c in db.all_rows('companies',campaign['id']):
            c['research_status']='completed'; db.put('companies',c)
        original=db.all_rows('companies'); jobs=db.all_rows('jobs')
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Fiches mémos et exports')
            next(b for b in at.button if b.label=='Préparer les exports').click().run()
            self.assertTrue(at.session_state['export_package']['signature'])
            _,rows=services.campaign_results(campaign['id'])
            id=next(c['id'] for c in rows if c['selection']=='shortlisted')
            # Un autre navigateur peut modifier la sélection sans vider cette session.
            services.set_selection_dismissed(campaign['id'],[id])
            at.run()
            self.assertFalse(at.exception)
            self.assertNotIn('export_package',at.session_state)
            self.assertTrue(any('La sélection ou les données ont changé' in x.value for x in at.info))
            navigate(at,'Campagne et classement')
            at.button(key='restore_selection_button_'+campaign['id']).click().run()
            self.assertFalse(at.exception)
            self.assertFalse(db.get('campaigns',campaign['id'])['selection_decisions'])
            at.multiselect(key='dismiss_ids_'+campaign['id']).set_value([id])
            next(b for b in at.button if b.label=='Écarter et recalculer le Top').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(db.get('campaigns',campaign['id'])['selection_decisions'][id]['dismissed'])
            next(s for s in at.selectbox if s.label=='Afficher').set_value('Écartées').run()
            self.assertFalse(at.exception)
            self.assertTrue(any('Classement recalculé' in x.value for x in at.success) or
                            at.selectbox(key='restore_selection_'+campaign['id']).value==id)
            network.assert_not_called(); launch.assert_not_called()
        self.assertEqual(db.all_rows('companies'),original); self.assertEqual(db.all_rows('jobs'),jobs)

    def test_research_progress_distinguishes_attempts_outcomes_and_unfinished_companies(self):
        from impact import db,services
        db.init()
        campaign=services.create_campaign('Salon compteurs','2027',[
            {'name':name,'category':'Produits alimentaires'}
            for name in ['Terminée A','Terminée B','Ambiguë','Erreur A','Erreur B']],'test')
        companies=db.all_rows('companies',campaign['id'])
        for company in companies:
            company['research_status']=('completed' if company['name'].startswith('Terminée') else
                                        'ambiguous' if company['name']=='Ambiguë' else 'error')
            db.put('companies',company)
        job=db.put('jobs',{'campaign_id':campaign['id'],'created':db.now(),'status':'completed_with_errors',
                           'done':3,'total':3,'errors':[{'company':'Erreur A','message':'Source inaccessible'},
                                                     {'company':'Erreur B','message':'Source inaccessible'}],
                           'elapsed_seconds':137283.45,'timing_complete':True,'timing_source':'recorded',
                           'message':'Recherche terminée. Les données sourcées sont prêtes à vérifier.'})
        before=db.all_rows('companies',campaign['id']); before_job=db.get('jobs',job['id'])
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            text='\n'.join(x.value for x in at.info)
            for value in ['5/5 exposants parcourus','toute la liste a été parcourue','2 recherches terminées',
                          '1 identité ambiguë','2 erreurs','0 non parcourus']:
                self.assertIn(value,text)
            self.assertFalse(any('2/5 entreprises recherchées' in x.value for x in at.info))
            self.assertTrue(any('Dernier lot terminé avec erreurs : 3/3 tentatives effectuées · 2 erreurs' in x.value
                                for x in at.caption))
            timing='\n'.join(x.value for x in at.markdown)
            self.assertIn('Temps cumulé connu',timing); self.assertIn('38 h 08 min',timing)
            self.assertNotIn('Au moins',timing)
            self.assertEqual(db.all_rows('companies',campaign['id']),before)
            self.assertEqual(db.get('jobs',job['id']),before_job)
            # Une reprise partielle ne doit pas être présentée comme un parcours complet.
            retry=next(c for c in companies if c['name']=='Erreur A')
            retry['research_status']='pending'; db.put('companies',retry)
            at.run()
            text='\n'.join(x.value for x in at.info)
            self.assertIn('4/5 exposants parcourus',text)
            self.assertIn('1 erreur',text); self.assertIn('1 non parcouru',text)
            self.assertNotIn('toute la liste a été parcourue',text)
            network.assert_not_called(); launch.assert_not_called()

    def test_score_guide_opens_on_click_uses_campaign_rules_and_keeps_running_job(self):
        from impact import db,services
        db.init()
        campaign=services.create_campaign('Salon explication','2027',[
            {'name':'Producteur Alpha','category':'Produits alimentaires','sector_fit':'true',
             'business_model':'industriel','city':'Lyon'},
            {'name':'À documenter','category':'Produits alimentaires'}],'test')
        campaign['criteria'].update(min_revenue_eur=20_000_000,min_margin_pct=12.5,tier1=85,tier2=65,
                                    weights={'sector':30,'international':10,'finance':25,'model':15,'city':5,'triggers':15})
        campaign['crm_review']={'empty_confirmed':True}; db.put('campaigns',campaign)
        job=db.put('jobs',{'campaign_id':campaign['id'],'status':'running','created':db.now(),
                           'done':1,'total':2,'message':'Recherche en cours','errors':[],'cancel':False})
        before=db.get('jobs',job['id'])
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch, \
             patch('impact.db.external_worker_alive',return_value=True):
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            self.assertFalse(any('<div class="score-guide-summary">' in x.value for x in at.markdown))
            at.button(key='open_score_guide').click().run()
            self.assertFalse(at.exception)
            content='\n'.join(x.value for x in at.markdown)
            self.assertIn('20 000 000 €',content); self.assertIn('12,5 %',content)
            self.assertIn('30 pts',content); self.assertIn('De 85 à 100',content)
            self.assertIn('De 65 à moins de 85',content)
            self.assertIn('30 / 30',content); self.assertIn('50 / 100',content)
            self.assertIn('Tier 3',content)
            other=next(c for c in db.all_rows('companies',campaign['id']) if c['name']=='À documenter')
            # AppTest rejoue l'application entière plutôt que le fragment du dialogue.
            at.selectbox(key='score_example_'+campaign['id']).set_value(other['id'])
            at.button(key='open_score_guide').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(any('0 / 100' in x.value for x in at.markdown))
            at.button(key='close_score_guide').click().run()
            self.assertFalse(at.exception)
            self.assertFalse(any('<div class="score-guide-summary">' in x.value for x in at.markdown))
            self.assertEqual(db.get('jobs',job['id']),before)
            network.assert_not_called(); launch.assert_not_called()

    def test_progressive_selection_is_immediate_and_local_quick_scope_is_explicit(self):
        from impact import db,services
        db.init()
        c=services.create_campaign('Salon rapide','2027',[
            {'name':'Producteur Alpha','category':'Produits alimentaires'},
            {'name':'Logiciel Beta','category':'Informatique'}],'test')
        c['crm_review']={'empty_confirmed':True}; db.put('campaigns',c)
        db.setting('ai_provider','ollama')
        db.setting('ai_options',{'ollama':{'model':'qwen3:1.7b','web_provider':'direct'}})
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            self.assertEqual(next(m.value for m in at.metric if m.label=='Présélection automatique'),'1 / 20')
            self.assertEqual(next(m.value for m in at.metric if m.label=='Critères documentés'),'0')
            self.assertEqual(next(m.value for m in at.metric if m.label=='Vérifiées manuellement'),'0')
            self.assertTrue(at.toggle(key='quick_'+c['id']).value)
            self.assertTrue(at.toggle(key='priority_'+c['id']).value)
            at.toggle(key='progressive_'+c['id']).set_value(False).run()
            self.assertFalse(db.get('campaigns',c['id'])['criteria']['progressive_selection'])
            at.toggle(key='progressive_'+c['id']).set_value(True).run()
            self.assertTrue(db.get('campaigns',c['id'])['criteria']['progressive_selection'])
            navigate(at,'Fiches mémos et exports')
            next(r for r in at.radio if r.label=='Contenu à exporter').set_value('Critères documentés').run()
            self.assertTrue(any('0 fiche' in x.value for x in at.caption))
            next(r for r in at.radio if r.label=='Contenu à exporter').set_value('Présélection automatique').run()
            self.assertTrue(any('1 fiche' in x.value for x in at.caption))
            self.assertFalse(at.exception)
            network.assert_not_called(); launch.assert_not_called()

    def test_missing_word_dependency_keeps_excel_csv_and_pdf_downloads_available(self):
        from impact import db,services
        db.init()
        campaign=services.create_campaign('Salon exports','2027',[
            {'name':'Producteur Alpha','category':'Produits alimentaires'}],'test')
        campaign['crm_review']={'empty_confirmed':True}; db.put('campaigns',campaign)
        with patch('impact.exports.document_bytes',side_effect=ModuleNotFoundError("No module named 'docx'",name='docx')), \
             patch('impact.exports.bundle_bytes') as bundle,patch('impact.providers.json_request') as network:
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Fiches mémos et exports')
            next(b for b in at.button if b.label=='Préparer les exports').click().run()
            self.assertFalse(at.exception)
            package=at.session_state['export_package']
            self.assertTrue(package['xlsx']); self.assertTrue(package['csv']); self.assertTrue(package['pdf'])
            self.assertIsNone(package['docx']); self.assertIsNone(package['zip'])
            self.assertTrue(any('python-docx' in w.value and 'Lancer_IMPACT.cmd' in w.value for w in at.warning))
            bundle.assert_not_called(); network.assert_not_called()

    def test_memo_tier_filter_and_empty_tier_preserve_export_scope(self):
        from datetime import datetime
        from impact import db,services
        db.init()
        core={'category':'Produits alimentaires','identity':'true','sector_fit':'true','business_model':'industriel',
              'revenue_eur':'12000000','finance_year':str(datetime.now().year-1),'finance_scope':'entite','city':'Paris'}
        c=services.create_campaign('Salon tiers','2027',[
            {'name':'Priorité haute',**core,'international':'true'},
            {'name':'Priorité moyenne',**core},
            {'name':'À approfondir','category':'Produits alimentaires','identity':'true','sector_fit':'true','business_model':'industriel','city':'Paris'}],'test')
        c['crm_review']={'empty_confirmed':True}; db.put('campaigns',c)
        for row in db.all_rows('companies',c['id']):
            row['research_status']='completed'; db.put('companies',row)
        with patch('impact.providers.json_request') as network:
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Fiches mémos et exports')
            self.assertFalse(at.exception)
            self.assertEqual([m.value for m in at.metric if m.label in {'Tier 1','Tier 2','Tier 3'}],['1','1','1'])
            next(s for s in at.selectbox if s.label=='Tier de l’aperçu').set_value(3).run()
            self.assertEqual(len(next(s for s in at.selectbox if s.label=='Aperçu de fiche').options),1)
            next(r for r in at.radio if r.label=='Contenu à exporter').set_value('Critères documentés').run()
            self.assertTrue(any('Aucune fiche dans ce tier' in x.value for x in at.info))
            next(b for b in at.button if b.label=='Préparer les exports').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(at.session_state['export_package']['zip'])
            network.assert_not_called()

    def test_sidebar_choices_apply_once_and_survive_navigation_and_reruns(self):
        from impact import db, services, providers
        import streamlit as st
        self.addCleanup(setattr,db,'DATA',db.DATA)
        self.addCleanup(st.cache_resource.clear)
        with tempfile.TemporaryDirectory(prefix='impact_sidebar_ui_') as data_dir, \
             patch.dict(os.environ,{'AI_PROVIDER':'openai','OPENAI_API_KEY':'',
                 'OLLAMA_API_KEY':'','GEMINI_API_KEY':'','WEB_SEARCH_PROVIDER':'ollama'}), \
             patch('impact.ai.request.urlopen') as network,patch('impact.services.start_job') as launch:
            db.DATA=Path(data_dir); st.cache_resource.clear(); db.init()
            first=services.create_campaign('Premier salon','2027',[{'name':'Alpha'}],'test')
            second=services.create_campaign('Second salon','2028',[{'name':'Beta'},{'name':'Gamma'}],'test')
            db.setting('ai_provider','gemini')
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            self.assertEqual(at.selectbox(key='ai_provider_choice').value,'gemini')
            for provider in ['ollama','openai','gemini']:
                # Une seule interaction suffit, sans deuxième clic ni deuxième soumission.
                at.selectbox(key='ai_provider_choice').set_value(provider).run()
                self.assertEqual(at.selectbox(key='ai_provider_choice').value,provider)
                self.assertEqual(db.setting('ai_provider'),provider)
                navigate(at,'Configuration IA')
                self.assertTrue(any(providers.LABELS[provider] in item.value for item in at.markdown))
                at.run()
                self.assertEqual(at.selectbox(key='ai_provider_choice').value,provider)
            for campaign in [second,first,second]:
                navigate(at,'Campagne et classement')
                at.selectbox(key='campaign_select').set_value(campaign['id']).run()
                self.assertEqual(at.session_state['active_campaign'],campaign['id'])
                self.assertTrue(any(campaign['name'] in item.value and 'hero' in item.value for item in at.markdown))
                self.assertEqual(next(m.value for m in at.metric if m.label=='Exposants'),
                                 str(len(db.all_rows('companies',campaign['id']))))
                navigate(at,'Fiches mémos et exports')
                at.run()
                self.assertEqual(at.selectbox(key='campaign_select').value,campaign['id'])
                self.assertEqual(at.session_state['active_campaign'],campaign['id'])
            # Le menu montre une seule page active et les confirmations restent explicites.
            navigate(at,'Importer des exposants')
            self.assertEqual([b.label for b in at.sidebar.button if b.proto.type=='primary'],['Importer des exposants'])
            navigate(at,'Critères et connaissances')
            self.assertEqual(len(at.checkbox),0)
            self.assertEqual(len(at.toggle),3)
            self.assertFalse(at.exception)
            network.assert_not_called(); launch.assert_not_called()

    def test_provider_selection_configuration_and_partial_free_estimates(self):
        from impact import db, services
        import streamlit as st
        self.addCleanup(setattr,db,'DATA',db.DATA)
        self.addCleanup(st.cache_resource.clear)
        with tempfile.TemporaryDirectory(prefix='impact_choices_ui_') as data_dir, \
             patch.dict(os.environ,{'AI_PROVIDER':'openai','OPENAI_API_KEY':'','OPENAI_MODEL':'gpt-5.5',
                 'OLLAMA_API_KEY':'','GEMINI_API_KEY':'','WEB_SEARCH_PROVIDER':'ollama',
                 'OLLAMA_MODEL':'qwen3:8b','GEMINI_MODEL':'gemini-3.5-flash-lite','GEMINI_TIER':'free'}), \
             patch('impact.ai.request.urlopen') as network,patch('impact.services.start_job') as launch:
            db.DATA=Path(data_dir); st.cache_resource.clear(); db.init()
            services.create_campaign('Salon choix IA','2027',[{'name':'Alpha'}],'test')
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Campagne et classement')
            at.selectbox(key='ai_provider_choice').set_value('ollama').run()
            self.assertFalse(at.exception)
            self.assertTrue(any('analyse seule' in str(d.value.columns) for d in at.dataframe))
            self.assertTrue(any('Quota disponible' in x.value for x in at.info))
            navigate(at,'Configuration IA')
            self.assertFalse(at.exception)
            with patch('impact.providers.local_models',return_value=['phi4:latest']):
                at.button(key='detect_ollama_models').click().run()
            next(b for b in at.button if b.label=='Utiliser ce modèle local').click().run()
            self.assertEqual(db.setting('ai_options')['ollama']['model'],'phi4:latest')
            at.selectbox(key='ai_provider_choice').set_value('gemini').run()
            self.assertFalse(at.exception)
            self.assertTrue(any('Google' in x.value for x in at.caption))
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            self.assertTrue(any('analyse seule' in str(d.value.columns) for d in at.dataframe))
            self.assertTrue(next(b for b in at.button if b.label=='Rechercher et préparer les fiches').disabled)
            network.assert_not_called(); launch.assert_not_called()
            st.cache_resource.clear()

    def test_cost_preview_is_free_and_updates_scope_before_launch(self):
        from impact import db, services
        import streamlit as st
        self.addCleanup(setattr,db,'DATA',db.DATA)
        self.addCleanup(st.cache_resource.clear)
        with tempfile.TemporaryDirectory(prefix='impact_estimate_ui_') as data_dir, \
             patch.dict(os.environ,{'OPENAI_API_KEY':'','OPENAI_MODEL':'gpt-5.5'}), \
             patch('impact.ai.request.urlopen') as network,patch('impact.services.start_job') as launch:
            db.DATA=Path(data_dir)
            st.cache_resource.clear(); db.init()
            campaign=services.create_campaign('Salon estimation','2027',[
                {'name':'Atelier Sucré'},{'name':'Glace du Littoral'},
                {'name':'Maison Boréale'},{'name':'Ancien Client'}],'test')
            completed=next(c for c in db.all_rows('companies',campaign['id']) if c['name']=='Glace du Littoral')
            completed['research_status']='completed'; db.put('companies',completed)
            db.put('crm',{'name':'Ancien Client','status':'client','contacts':[]})
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            def preview(): return next(d.value for d in at.dataframe if 'Périmètre' in d.value.columns)
            self.assertEqual(preview()['Entreprises'].tolist(),[2,2])
            at.number_input(key='research_limit_'+campaign['id']).set_value(1).run()
            self.assertEqual(preview()['Entreprises'].tolist(),[1,2])
            self.assertEqual(preview()['Simulation centrale'].tolist(),['0,30 $','0,60 $'])
            next(c for c in at.toggle if c.label=='Rechercher à nouveau les entreprises déjà traitées').set_value(True).run()
            self.assertEqual(preview()['Entreprises'].tolist(),[1,3])
            self.assertTrue(next(b for b in at.button if b.label=='Rechercher et préparer les fiches').disabled)
            # Un modèle inconnu exige ses tarifs, sans supposer le prix de GPT-5.5.
            with patch.dict(os.environ,{'OPENAI_MODEL':'private-model'}):
                at.run()
                self.assertFalse(at.exception)
                self.assertFalse(any('Périmètre' in d.value.columns for d in at.dataframe))
                at.number_input(key='estimate_input_private-model').set_value(1.0)
                at.number_input(key='estimate_output_private-model').set_value(2.0).run()
                self.assertEqual(preview()['Simulation centrale'].tolist(),['0,08 $','0,24 $'])
            network.assert_not_called(); launch.assert_not_called()
            self.assertEqual(db.all_rows('jobs'),[])
            st.cache_resource.clear()

    def test_delete_campaign_confirmation_and_active_selection_cleanup(self):
        from impact import db, services
        import streamlit as st
        self.addCleanup(setattr,db,'DATA',db.DATA)
        self.addCleanup(st.cache_resource.clear)
        with tempfile.TemporaryDirectory(prefix='impact_delete_ui_') as data_dir:
            db.DATA=Path(data_dir)
            st.cache_resource.clear()
            db.init()
            keep=services.create_campaign('Salon conservé','2027',[{'name':'Gamma'}],'test')
            remove=services.create_campaign('Salon supprimé','2027',[{'name':'Alpha'}],'test')
            crm=db.put('crm',{'name':'Client conservé','status':'client','contacts':[]})
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30)
            at.session_state['active_campaign']=remove['id']
            at.session_state['export_package']={'campaign_id':remove['id']}
            at.run()
            self.assertFalse(at.exception)
            at.button(key='delete_'+remove['id']).click().run()
            self.assertTrue(at.button(key='delete_confirm_button_'+remove['id']).disabled)
            at.button(key='delete_cancel_'+remove['id']).click().run()
            self.assertEqual(db.get('campaigns',remove['id'])['name'],'Salon supprimé')
            at.button(key='delete_'+remove['id']).click().run()
            self.assertFalse(at.toggle(key='delete_confirm_'+remove['id']).value)
            at.toggle(key='delete_confirm_'+remove['id']).set_value(True).run()
            at.button(key='delete_confirm_button_'+remove['id']).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.session_state['active_campaign'],keep['id'])
            self.assertNotIn('export_package',at.session_state)
            self.assertTrue(any('supprimée' in s.value for s in at.success))
            self.assertEqual(db.get('crm',crm['id'])['name'],'Client conservé')
            # Le même contrôle fonctionne depuis la campagne et pour la dernière campagne.
            navigate(at,'Campagne et classement')
            self.assertFalse(at.exception)
            at.toggle(key='delete_confirm_'+keep['id']).set_value(True).run()
            at.button(key='delete_confirm_button_'+keep['id']).click().run()
            self.assertFalse(at.exception)
            self.assertEqual(db.all_rows('campaigns'),[])
            self.assertNotIn('active_campaign',at.session_state)
            self.assertFalse(any(s.label=='Campagne active' for s in at.selectbox))
            st.cache_resource.clear()

    def test_table_preview_and_editors(self):
        from impact import db, services
        from impact.importers import parse_file
        import streamlit as st
        self.addCleanup(setattr,db,'DATA',db.DATA)
        self.addCleanup(st.cache_resource.clear)
        with tempfile.TemporaryDirectory(prefix='impact_tables_') as tables_dir:
            db.DATA=Path(tables_dir)
            st.cache_resource.clear()
            db.init()
            parsed=parse_file('exposants.csv',b'Company,Website,Stand\nTest Exposant,https://exposant.example,A12\n')
            tab=parsed['tables'][0]
            services.import_campaign(parsed,0,tab['mapping'],'Salon de test','2027')
            db.put('crm',{'id':db.uid(),'name':'Client existant','website':'https://client.example','siren':'','status':'client','contacts':[]})
            at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
            # Aperçu import, tableau CRM, éditeur CRM et éditeur des contacts.
            for page in ['Campagne et classement','Clients et prospects','Importer des exposants']:
                navigate(at,page)
                self.assertFalse(at.exception,f'{page}: {at.exception}')
                if page=='Importer des exposants':
                    at.selectbox(key='exhibitors_example').set_value('SIRHA_Marseille_exemple.xlsx').run()
                    self.assertFalse(at.exception)
                    self.assertTrue(any('117 lignes lues' in c.value for c in at.caption))
            st.cache_resource.clear()

    def test_pages_import_real_example_and_exports(self):
        from impact import db
        import streamlit as st
        st.cache_resource.clear()
        at=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
        self.assertFalse(at.exception)
        for page in ['Clients et prospects','Critères et connaissances','Configuration IA','Importer des exposants']:
            navigate(at,page)
            self.assertFalse(at.exception,f'{page}: {at.exception}')
        at.selectbox(key='exhibitors_example').set_value('SIRHA_Marseille_exemple.xlsx').run()
        self.assertFalse(at.exception)
        at.text_input(key='new_salon').set_value('Salon de test').run()
        at.text_input(key='new_edition').set_value('2027').run()
        at.toggle(key='import_checked').set_value(True).run()
        next(b for b in at.button if b.label=='Créer la campagne').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any('117' in str(m.value) for m in at.metric))
        navigate(at,'Fiches mémos et exports')
        self.assertFalse(at.exception)
        next(b for b in at.button if b.label=='Préparer les exports').click().run()
        self.assertFalse(at.exception)
        navigate(at,'Campagne et classement')
        self.assertFalse(at.exception)
        # Qualification de bout en bout à partir d'une saisie explicite et d'une base CRM.
        navigate(at,'Clients et prospects')
        next(w for w in at.text_input if w.label=='Entreprise').set_value('Ancien Client de test')
        next(b for b in at.button if b.label=='Ajouter à la base').click().run()
        self.assertFalse(at.exception)
        navigate(at,'Campagne et classement')
        for label in ['Identité de l’entreprise confirmée','Activité conforme aux secteurs cibles']:
            next(w for w in at.selectbox if w.label==label).set_value('oui')
        next(w for w in at.text_input if w.label=='Modèle économique').set_value('industriel')
        next(w for w in at.text_input if w.label=='CA en EUR').set_value('12000000')
        next(w for w in at.text_input if w.label=='Exercice financier').set_value('2025')
        next(w for w in at.selectbox if w.label=='Périmètre financier').set_value('entite')
        next(w for w in at.multiselect if w.label=='Informations vérifiées à valider').set_value(['identity','sector_fit','business_model','revenue_eur','finance_year','finance_scope'])
        next(w for w in at.toggle if w.label=='J’ai contrôlé les sources des champs sélectionnés et la correspondance de l’entreprise.').set_value(True)
        next(b for b in at.button if b.label=='Enregistrer la vérification').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(next(m for m in at.metric if m.label=='Vérifiées manuellement').value,'1')
        navigate(at,'Fiches mémos et exports')
        next(b for b in at.button if b.label=='Préparer les exports').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(at.session_state['export_package']['zip'])

if __name__=='__main__': unittest.main(verbosity=2)
