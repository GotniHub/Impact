import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from impact import auth

PASSWORD='Test-only Correct Password 2026!'


class AuthTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='impact_auth_test_'); self.addCleanup(temp.cleanup)
        self.now=1_800_000_000.0
        self.path=Path(temp.name)/'auth.sqlite3'
        self.store=auth.AuthStore(self.path,clock=lambda:self.now)
        self.store.add_user('test@example.org',PASSWORD)

    def test_password_hash_uses_independent_salts_and_constant_time_verification(self):
        first=auth.hash_password(PASSWORD); second=auth.hash_password(PASSWORD)
        self.assertNotEqual(first,second)
        self.assertTrue(first.startswith('pbkdf2_sha256$600000$'))
        self.assertTrue(auth.verify_password(PASSWORD,first))
        self.assertFalse(auth.verify_password(PASSWORD+'x',first))
        self.assertFalse(auth.verify_password(PASSWORD,'invalid'))
        with self.assertRaises(ValueError): auth.hash_password('too short')
        with self.assertRaises(ValueError): auth.hash_password('x'*1025)

    def test_unknown_wrong_and_disabled_credentials_have_same_response(self):
        wrong=self.store.authenticate('test@example.org','Wrong Password')
        unknown=self.store.authenticate('unknown@example.org','Wrong Password')
        self.store.set_enabled('test@example.org',False)
        disabled=self.store.authenticate('test@example.org',PASSWORD)
        self.assertEqual(wrong,{'error':auth.INVALID_LOGIN})
        self.assertEqual(wrong,unknown); self.assertEqual(wrong,disabled)

    def test_credentials_and_session_tokens_are_never_stored_in_plaintext(self):
        login=self.store.authenticate(' TEST@EXAMPLE.ORG ',PASSWORD)
        self.assertEqual(login['email'],'test@example.org')
        self.assertEqual(self.store.user_for_session(login['token']),'test@example.org')
        self.assertNotIn(PASSWORD.encode(),self.path.read_bytes())
        self.assertNotIn(login['token'].encode(),self.path.read_bytes())
        with self.store.connection() as conn:
            self.assertEqual(conn.execute('SELECT token_hash FROM sessions').fetchone()[0],auth.token_hash(login['token']))
        self.assertEqual(set(self.store.list_users()[0]),{'email','enabled','created'})

    def test_lockout_is_shared_across_connections_and_cannot_reset_by_case_or_refresh(self):
        for _ in range(auth.MAX_FAILURES): self.store.authenticate('TEST@example.org','wrong')
        other=auth.AuthStore(self.path,clock=lambda:self.now)
        self.assertEqual(other.authenticate(' test@EXAMPLE.ORG ',PASSWORD),{'error':auth.LOCKED_LOGIN})
        self.now+=auth.LOCK_SECONDS+1
        self.assertIn('token',other.authenticate('test@example.org',PASSWORD))

    def test_simultaneous_failures_are_counted_atomically(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self.store.authenticate('test@example.org','wrong'),range(6)))
        self.assertEqual(sum(r.get('error')==auth.INVALID_LOGIN for r in results),5)
        self.assertEqual(sum(r.get('error')==auth.LOCKED_LOGIN for r in results),1)

    def test_global_attempt_limit_bounds_unknown_user_probes(self):
        with patch('impact.auth.verify_password',return_value=False) as verify:
            for i in range(auth.GLOBAL_ATTEMPTS): self.store.authenticate(f'guess{i}@example.org','wrong')
            self.assertEqual(self.store.authenticate('test@example.org',PASSWORD),{'error':auth.LOCKED_LOGIN})
            self.assertEqual(verify.call_count,auth.GLOBAL_ATTEMPTS)
        self.now+=auth.GLOBAL_WINDOW+1
        self.assertIn('token',self.store.authenticate('test@example.org',PASSWORD))

    def test_idle_expiry_is_not_extended_by_automatic_read_checks(self):
        token=self.store.authenticate('test@example.org',PASSWORD)['token']
        self.now+=auth.IDLE_SECONDS-1
        self.assertEqual(self.store.user_for_session(token),'test@example.org')
        self.now+=1
        self.assertIsNone(self.store.user_for_session(token))
        self.assertIsNone(self.store.user_for_session('forged token'))

    def test_absolute_expiry_applies_despite_recent_user_activity(self):
        token=self.store.authenticate('test@example.org',PASSWORD)['token']
        start=self.now
        while self.now+auth.IDLE_SECONDS-1<start+auth.SESSION_SECONDS:
            self.now+=auth.IDLE_SECONDS-1
            self.assertEqual(self.store.user_for_session(token,touch=True),'test@example.org')
        self.now=start+auth.SESSION_SECONDS
        self.assertIsNone(self.store.user_for_session(token,touch=True))

    def test_multiple_accounts_logout_reset_and_disable_revoke_only_expected_sessions(self):
        self.store.add_user('other@example.org',PASSWORD)
        first=self.store.authenticate('test@example.org',PASSWORD)['token']
        second=self.store.authenticate('test@example.org',PASSWORD)['token']
        other=self.store.authenticate('other@example.org',PASSWORD)['token']
        self.store.logout(first)
        self.assertIsNone(self.store.user_for_session(first))
        self.assertEqual(self.store.user_for_session(second),'test@example.org')
        self.store.reset_password('test@example.org',PASSWORD+'new')
        self.assertIsNone(self.store.user_for_session(second))
        new=self.store.authenticate('test@example.org',PASSWORD+'new')['token']
        self.store.set_enabled('test@example.org',False)
        self.assertIsNone(self.store.user_for_session(new))
        self.assertEqual(self.store.user_for_session(other),'other@example.org')
        self.assertEqual(len(self.store.list_users()),2)
        with self.assertRaises(ValueError): self.store.add_user('OTHER@example.org',PASSWORD)

    def test_missing_accounts_have_no_default_access(self):
        empty=auth.AuthStore(self.path.parent/'empty.sqlite3')
        self.assertFalse(empty.has_users())
        self.assertEqual(empty.authenticate('triva@adv-sud.fr',PASSWORD),{'error':auth.INVALID_LOGIN})


if __name__=='__main__': unittest.main(verbosity=2)
