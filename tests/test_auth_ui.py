import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from streamlit.testing.v1 import AppTest
from impact import db,services,auth,auth_ui

PASSWORD='Test-only Correct Password 2026!'
APP=str(Path(__file__).resolve().parents[1]/'app.py')


class AuthUITests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_auth_ui_'); self.addCleanup(temp.cleanup)
        data=patch.object(db,'DATA',Path(temp.name)); data.start(); self.addCleanup(data.stop)
        st.cache_resource.clear(); self.addCleanup(st.cache_resource.clear)
        db.init()
        self.campaign=services.create_campaign('Campagne confidentielle','2027',[{'name':'Entreprise confidentielle'}],'test')
        self.store=auth.AuthStore(db.DATA/'auth.sqlite3')
        self.store.add_user('test@example.org',PASSWORD)

    def login(self,app):
        app.text_input(key=auth_ui.LOGIN_EMAIL).set_value(' TEST@example.org ')
        app.text_input(key=auth_ui.LOGIN_PASSWORD).set_value(PASSWORD)
        next(b for b in app.button if b.label=='Se connecter').click().run()

    def test_anonymous_access_stops_before_private_data_or_actions_even_with_forged_flags(self):
        at=AppTest.from_file(APP,default_timeout=30)
        at.session_state['authenticated']=True
        at.session_state['navigation']='Configuration IA'
        at.query_params['authenticated']='true'
        at.query_params['page']='Campagne et classement'
        with patch('impact.db.all_rows',side_effect=AssertionError('Private read before login')), \
             patch('impact.db.init',side_effect=AssertionError('Business init before login')), \
             patch('impact.services.start_job') as launch:
            at.run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.sidebar.button),0)
        self.assertEqual(len(at.dataframe),0)
        self.assertEqual(at.text_input(key=auth_ui.LOGIN_PASSWORD).proto.type,1)
        content=' '.join(str(x.value) for x in at.markdown)
        self.assertIn('Bienvenue sur IMPACT',content)
        self.assertNotIn('Campagne confidentielle',content)
        self.assertNotIn('Entreprise confidentielle',content)
        launch.assert_not_called()

    def test_real_login_once_and_logout_revoke_session_and_clear_private_buffers(self):
        before=db.all_rows('companies'); jobs=db.all_rows('jobs')
        with patch('impact.providers.json_request') as network,patch('impact.services.start_job') as launch:
            at=AppTest.from_file(APP,default_timeout=30).run()
            self.login(at)
            self.assertFalse(at.exception)
            self.assertTrue(any(b.label=='Campagne et classement' for b in at.sidebar.button))
            token=at.session_state[auth_ui.SESSION_KEY]
            self.assertEqual(self.store.user_for_session(token),'test@example.org')
            self.assertNotIn(auth_ui.LOGIN_PASSWORD,at.session_state)
            at.session_state['export_package']={'xlsx':b'private'}
            at.session_state['exhibitors_parsed']={'private':'import'}
            next(b for b in at.sidebar.button if b.label=='Se déconnecter').click().run()
            self.assertFalse(at.exception)
            self.assertIsNone(self.store.user_for_session(token))
            for key in (auth_ui.SESSION_KEY,'export_package','exhibitors_parsed','active_campaign'):
                self.assertNotIn(key,at.session_state)
            self.assertTrue(any(b.label=='Se connecter' for b in at.button))
            self.assertEqual(len(at.sidebar.button),0)
            network.assert_not_called(); launch.assert_not_called()
        self.assertEqual(db.all_rows('companies'),before); self.assertEqual(db.all_rows('jobs'),jobs)

    def test_wrong_login_is_generic_and_password_is_cleared(self):
        at=AppTest.from_file(APP,default_timeout=30).run()
        for email in ('test@example.org','unknown@example.org'):
            at.text_input(key=auth_ui.LOGIN_EMAIL).set_value(email)
            at.text_input(key=auth_ui.LOGIN_PASSWORD).set_value('wrong')
            next(b for b in at.button if b.label=='Se connecter').click().run()
            self.assertFalse(at.exception)
            self.assertTrue(any(x.value==auth.INVALID_LOGIN for x in at.error))
            self.assertEqual(at.text_input(key=auth_ui.LOGIN_PASSWORD).value,'')
            self.assertNotIn(auth_ui.SESSION_KEY,at.session_state)

    def test_server_revocation_blocks_next_interaction_before_loading_campaigns(self):
        at=AppTest.from_file(APP,default_timeout=30).run(); self.login(at)
        token=at.session_state[auth_ui.SESSION_KEY]
        self.store.set_enabled('test@example.org',False)
        at.session_state['export_package']={'pdf':b'private'}
        with patch('impact.db.all_rows',side_effect=AssertionError('Revoked session read')):
            at.run()
        self.assertFalse(at.exception)
        self.assertNotIn('export_package',at.session_state)
        self.assertIsNone(self.store.user_for_session(token))
        self.assertEqual(len(at.sidebar.button),0)
        self.assertTrue(next(b for b in at.button if b.label=='Se connecter').disabled)

    def test_missing_configuration_fails_closed(self):
        self.store.set_enabled('test@example.org',False)
        at=AppTest.from_file(APP,default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.sidebar.button),0)
        self.assertTrue(next(b for b in at.button if b.label=='Se connecter').disabled)
        self.assertTrue(any('Contactez votre administrateur' in e.value for e in at.error))

    def test_fragment_guard_checks_revocation_and_requests_full_rerun(self):
        state={auth_ui.SESSION_KEY:'stale','export_package':b'private','navigation':'Configuration IA'}
        class FullRerun(Exception): pass
        with patch.object(auth_ui.st,'session_state',state), \
             patch('impact.auth_ui.authenticated_user',return_value=None), \
             patch('impact.auth_ui.st.rerun',side_effect=FullRerun) as rerun:
            with self.assertRaises(FullRerun): auth_ui.guard_session()
            self.assertEqual(state,{})
            rerun.assert_called_once_with(scope='app')


if __name__=='__main__': unittest.main(verbosity=2)
