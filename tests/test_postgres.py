"""Intégration réelle, activée uniquement avec une adresse de test explicite.

Une base jetable propre à cette classe est créée ; aucune table de la base fournie
dans IMPACT_TEST_DATABASE_URL n'est modifiée.
"""
import json
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit,urlunsplit

from impact import auth,db,services,storage
from tools.migrate_to_supabase import migrate,read_snapshot,fingerprint

TEST_URL=os.environ.get('IMPACT_TEST_DATABASE_URL','')
PASSWORD='Postgres integration password 2026!'


@unittest.skipUnless(TEST_URL,'IMPACT_TEST_DATABASE_URL non configuré : PostgreSQL non testé ici')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        cls.database='impact_test_'+uuid.uuid4().hex
        with psycopg.connect(TEST_URL,autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.database)))
            # Simuler les rôles API Supabase pour contrôler leurs droits.
            for role in ('anon','authenticated'):
                if not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone():
                    conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(role)))
        p=urlsplit(TEST_URL)
        cls.url=urlunsplit((p.scheme,p.netloc,'/'+cls.database,p.query,p.fragment))

    @classmethod
    def tearDownClass(cls):
        import psycopg
        from psycopg import sql
        storage._pool(cls.url).close(); storage._pool.cache_clear()
        with psycopg.connect(TEST_URL,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(cls.database)))

    def setUp(self):
        import psycopg
        with psycopg.connect(self.url,autocommit=True) as conn:
            conn.execute('DROP SCHEMA IF EXISTS impact CASCADE')
            conn.execute('DROP SCHEMA IF EXISTS impact_auth CASCADE')
        env=patch.dict(os.environ,{'IMPACT_DATABASE_URL':self.url,'IMPACT_AUTH_USERS_JSON':''})
        env.start(); self.addCleanup(env.stop)
        self.temp=tempfile.TemporaryDirectory(prefix='impact_pg_source_'); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'auth.sqlite3'
        db.init(recover_jobs=False)

    def store(self,**kwargs): return auth.AuthStore(self.path,database_url=self.url,**kwargs)

    def test_campaign_crud_upsert_settings_cache_and_foreign_keys(self):
        import psycopg
        campaign=services.create_campaign('Salon PostgreSQL','2027',[{'name':"L'été ? 100 %"}],'fixture')
        company=db.all_rows('companies',campaign['id'])[0]
        self.assertEqual(db.company_count(campaign['id']),1)
        company['research_status']='completed'; db.put('companies',company)
        self.assertEqual(db.company_progress(campaign['id']),{'total':1,'completed':1,'ambiguous':0,'error':0,'pending':0,'processed':1})
        campaign['source']='Updated'; db.put('campaigns',campaign)
        self.assertEqual(db.get('companies',company['id'])['name'],"L'été ? 100 %")
        db.setting('test',{'quoted':'? % é'}); db.setting('test',{'quoted':'updated'})
        self.assertEqual(db.setting('test'),{'quoted':'updated'})
        cache=db.put('cache',{'key':'key','result':{'source':'é ?'}})
        cache['result']['source']='new'; db.put('cache',cache)
        self.assertEqual(db.get('cache','key')['result']['source'],'new')
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with db.connection() as conn:
                conn.execute('UPDATE campaigns SET name=? WHERE id=?',('Rollback',campaign['id']))
                conn.execute('INSERT INTO companies VALUES(?,?,?)',('bad','missing','{}'))
        with db.connection() as conn:
            self.assertEqual(conn.execute('SELECT name FROM campaigns WHERE id=?',(campaign['id'],)).fetchone()[0],'Salon PostgreSQL')
        services.delete_campaign(campaign['id'])
        self.assertEqual(db.all_rows('companies'),[])
        self.assertEqual(db.get('cache','key')['result']['source'],'new')

    def test_remote_auth_persists_across_instances_and_rejects_duplicate_account(self):
        first=self.store(); first.add_user('test@example.org',PASSWORD)
        with self.assertRaisesRegex(ValueError,'existe déjà'): first.add_user('test@example.org',PASSWORD)
        token=first.authenticate('test@example.org',PASSWORD)['token']
        other=self.store()
        self.assertEqual(other.user_for_session(token),'test@example.org')
        other.logout(token); self.assertIsNone(first.user_for_session(token))
        self.assertFalse(self.path.exists())

    def test_concurrent_auth_failures_and_idle_timing_use_precise_timestamps(self):
        now=[1_800_000_000.25]
        store=self.store(clock=lambda:now[0]); store.add_user('test@example.org',PASSWORD)
        with ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self.store(clock=lambda:now[0]).authenticate('TEST@example.org','wrong'),range(6)))
        self.assertEqual(sum(r.get('error')==auth.INVALID_LOGIN for r in results),5)
        self.assertEqual(sum(r.get('error')==auth.LOCKED_LOGIN for r in results),1)
        now[0]+=auth.LOCK_SECONDS+1
        token=store.authenticate('test@example.org',PASSWORD)['token']
        now[0]+=auth.IDLE_SECONDS-0.5
        self.assertEqual(store.user_for_session(token),'test@example.org')
        now[0]+=0.6
        self.assertIsNone(store.user_for_session(token))

    def test_schemas_and_tables_are_not_accessible_to_supabase_api_roles(self):
        self.store()
        with db.connection() as conn:
            for schema in ('impact','impact_auth'):
                for role in ('anon','authenticated'):
                    self.assertFalse(conn.execute('SELECT has_schema_privilege(?, ?, ?)',(role,schema,'USAGE')).fetchone()[0])
            rows=conn.execute("SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid WHERE n.nspname IN ('impact','impact_auth') AND c.relkind='r'").fetchall()
            self.assertEqual(len(rows),12)
            self.assertTrue(all(r[0] for r in rows))

    def source(self):
        target=Path(self.temp.name)/'source'
        with patch.dict(os.environ,{'IMPACT_DATABASE_URL':''}),patch.object(db,'DATA',target):
            db.init(recover_jobs=False)
            campaign=services.create_campaign('Résultats conservés','2027',[{'name':'Exposant existant'}],'local')
            company=db.all_rows('companies')[0]
            company.update(research_status='completed',memo={'pitch':'Résultat existant sans IA'})
            db.put('companies',company)
            db.put('jobs',{'campaign_id':campaign['id'],'status':'completed','done':1,'total':1})
            db.put('cache',{'key':'cached','result':{'memo':{'pitch':'Texte sauvegardé'}}})
            db.setting('ai_provider','gemini')
        return target/'impact.sqlite3'

    def test_migration_preserves_every_value_and_is_idempotent(self):
        source=self.source(); before=fingerprint(read_snapshot(source))
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            result=migrate(source,url=self.url,apply=True)
            self.assertTrue(result['verified']); self.assertEqual(result['ai_calls'],0)
            self.assertEqual(result['source_sha256'],before)
            self.assertEqual(db.all_rows('companies')[0]['memo']['pitch'],'Résultat existant sans IA')
            self.assertEqual(db.setting('ai_provider'),'gemini')
            self.assertTrue(migrate(source,url=self.url,apply=True)['already_imported'])
            self.assertEqual(len(db.all_rows('companies')),1)
            network.assert_not_called(); launch.assert_not_called()
        self.assertEqual(fingerprint(read_snapshot(source)),before)
        db.audit('after_migration',{})
        self.assertEqual(db.audit_rows()[0]['action'],'after_migration')

    def test_migration_refuses_to_overwrite_an_existing_campaign(self):
        source=self.source()
        campaign=services.create_campaign('À conserver','2027',[{'name':'Do not overwrite'}],'remote')
        with self.assertRaisesRegex(ValueError,'déjà des données'): migrate(source,url=self.url,apply=True)
        self.assertEqual(len(db.all_rows('campaigns')),1)
        self.assertEqual(db.get('campaigns',campaign['id'])['name'],'À conserver')

    def test_migration_verification_failure_rolls_back_all_copied_rows(self):
        source=self.source(); before=fingerprint(read_snapshot(source))
        with patch('tools.migrate_to_supabase.fingerprint',side_effect=[before,'different']):
            with self.assertRaisesRegex(RuntimeError,'Vérification'): migrate(source,url=self.url,apply=True)
        self.assertEqual(db.all_rows('campaigns'),[])
        self.assertIsNone(db.setting('sqlite_migration'))
        self.assertIsNone(db.setting('ai_provider'))

    def test_ui_login_and_campaign_read_use_remote_database(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from impact import auth_ui
        accounts=[{'email':'test@example.org','password_hash':auth.hash_password(PASSWORD)}]
        services.create_campaign('Campagne distante','2027',[{'name':'Visible après connexion'}],'remote')
        with patch.dict(os.environ,{'IMPACT_AUTH_USERS_JSON':json.dumps(accounts)}),patch.object(db,'DATA',Path(self.temp.name)/'empty_local'):
            st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)
            with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
                app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
                self.assertFalse(app.exception)
                app.text_input(key=auth_ui.LOGIN_EMAIL).set_value('test@example.org')
                app.text_input(key=auth_ui.LOGIN_PASSWORD).set_value(PASSWORD)
                next(b for b in app.button if b.label=='Se connecter').click().run()
                self.assertFalse(app.exception)
                self.assertTrue(any('Campagne distante' in str(m.value) for m in app.markdown))
                self.assertFalse((db.DATA/'impact.sqlite3').exists())
                self.assertFalse((db.DATA/'auth.sqlite3').exists())
                network.assert_not_called(); launch.assert_not_called()


if __name__=='__main__': unittest.main(verbosity=2)
