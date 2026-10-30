import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest
from impact import auth,auth_ui,db
from tools.export_cloud_login import export_cloud_login

PASSWORD='Cloud test password 2026!'
APP=str(Path(__file__).resolve().parents[1]/'app.py')


class CloudLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded=auth.hash_password(PASSWORD)

    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_cloud_'); self.addCleanup(temp.cleanup)
        self.data=Path(temp.name)
        self.path=self.data/'auth.sqlite3'
        self.store=auth.AuthStore(self.path)
        self.accounts=[{'email':'test@example.org','password_hash':self.encoded}]

    def test_fresh_cloud_database_uses_existing_password_hash(self):
        self.store.sync_users(self.accounts)
        login=self.store.authenticate('test@example.org',PASSWORD)
        self.assertEqual(login['email'],'test@example.org')
        self.assertNotIn(PASSWORD.encode(),self.path.read_bytes())
        # Un redémarrage avec les mêmes secrets conserve une session valide.
        other=auth.AuthStore(self.path); other.sync_users(self.accounts)
        self.assertEqual(other.user_for_session(login['token']),'test@example.org')

    def test_secret_update_revokes_changed_and_removed_accounts(self):
        self.store.sync_users(self.accounts+[{'email':'second@example.org','password_hash':self.encoded}])
        first=self.store.authenticate('test@example.org',PASSWORD)['token']
        second=self.store.authenticate('second@example.org',PASSWORD)['token']
        replacement=auth.hash_password('New cloud password 2026!')
        self.store.sync_users([{'email':'test@example.org','password_hash':replacement}])
        self.assertIsNone(self.store.user_for_session(first))
        self.assertIsNone(self.store.user_for_session(second))
        self.assertEqual(self.store.authenticate('second@example.org',PASSWORD),{'error':auth.INVALID_LOGIN})
        self.assertIn('token',self.store.authenticate('test@example.org','New cloud password 2026!'))

    def test_invalid_secrets_never_partially_change_database(self):
        self.store.sync_users(self.accounts)
        token=self.store.authenticate('test@example.org',PASSWORD)['token']
        bads=[[],{},[{'email':'bad','password_hash':self.encoded}],
              self.accounts+[{'email':'other@example.org','password_hash':'plaintext'}],
              self.accounts+[{'email':' TEST@example.org ','password_hash':self.encoded}],
              [{'email':'test@example.org','password_hash':self.encoded,'enabled':'false'}],
              [{'email':'test@example.org','password_hash':self.encoded,'password':PASSWORD}]]
        for accounts in bads:
            with self.subTest(accounts_type=type(accounts).__name__):
                with self.assertRaises(ValueError): self.store.sync_users(accounts)
                self.assertEqual(self.store.user_for_session(token),'test@example.org')
                self.assertEqual(len(self.store.list_users()),1)

    def test_secret_can_disable_account(self):
        self.store.sync_users(self.accounts)
        token=self.store.authenticate('test@example.org',PASSWORD)['token']
        self.store.sync_users([{**self.accounts[0],'enabled':False}])
        self.assertFalse(self.store.has_users())
        self.assertIsNone(self.store.user_for_session(token))

    def test_export_is_read_only_contains_no_password_and_round_trips_toml(self):
        import tomllib
        self.store.add_user('test@example.org',PASSWORD)
        self.store.add_user('disabled@example.org',PASSWORD)
        self.store.set_enabled('disabled@example.org',False)
        with closing(sqlite3.connect(self.path)) as conn: before=list(conn.iterdump())
        target=self.data/'private'/'secrets.toml'
        self.assertEqual(export_cloud_login(self.path,target),1)
        content=target.read_text(encoding='utf-8')
        self.assertNotIn(PASSWORD,content)
        accounts=json.loads(tomllib.loads(content)['IMPACT_AUTH_USERS_JSON'])
        self.assertEqual([a['email'] for a in accounts],['test@example.org'])
        self.assertTrue(auth.verify_password(PASSWORD,accounts[0]['password_hash']))
        with closing(sqlite3.connect(self.path)) as conn: self.assertEqual(list(conn.iterdump()),before)

    def test_cloud_ui_allows_login_without_local_users_and_no_automatic_ai_calls(self):
        with patch.object(db,'DATA',self.data), \
             patch.dict(os.environ,{'IMPACT_AUTH_USERS_JSON':json.dumps(self.accounts)}), \
             patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)
            with patch('impact.db.all_rows',side_effect=AssertionError('Private read before login')):
                app=AppTest.from_file(APP,default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertFalse(next(b for b in app.button if b.label=='Se connecter').disabled)
            app.text_input(key=auth_ui.LOGIN_EMAIL).set_value('test@example.org')
            app.text_input(key=auth_ui.LOGIN_PASSWORD).set_value(PASSWORD)
            next(b for b in app.button if b.label=='Se connecter').click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any(b.label=='Se déconnecter' for b in app.sidebar.button))
            self.assertEqual(db.all_rows('campaigns'),[])
            network.assert_not_called(); launch.assert_not_called()

    def test_malformed_secret_fails_closed_even_with_existing_user(self):
        self.store.add_user('test@example.org',PASSWORD)
        token=self.store.authenticate('test@example.org',PASSWORD)['token']
        with patch.object(db,'DATA',self.data),patch.dict(os.environ,{'IMPACT_AUTH_USERS_JSON':'not JSON'}), \
             patch('impact.db.all_rows',side_effect=AssertionError('Private read before login')):
            st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)
            app=AppTest.from_file(APP,default_timeout=30)
            app.session_state[auth_ui.SESSION_KEY]=token
            app.run()
            self.assertFalse(app.exception)
            self.assertNotIn(auth_ui.SESSION_KEY,app.session_state)
            self.assertTrue(next(b for b in app.button if b.label=='Se connecter').disabled)


if __name__=='__main__': unittest.main(verbosity=2)
