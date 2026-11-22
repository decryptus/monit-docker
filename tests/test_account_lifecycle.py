import hashlib
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from httpdis.authentication import AuthenticationDenied
from monit_docker.adapters.local_auth import Accounts
from monit_docker.account_lifecycle import AccountLifecycle
from test_local_auth import FastPasswords, PASSWORD

NEW_PASSWORD = 'replacement-password-123'

class AccountLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = [1000]
        self.accounts = Accounts(Path(directory.name) / 'auth.db', create=True,
                                 passwords=FastPasswords(), clock=lambda: self.now[0])
        self.addCleanup(self.accounts.close)
        self.accounts.provision('alice@example.com', PASSWORD, 'viewer')
        self.lifecycle = AccountLifecycle(self.accounts)

    def test_profile_is_persistent_and_cannot_change_role(self):
        self.lifecycle.update('alice@example.com', {'display_name': 'Alice', 'language': 'fr'})
        self.assertEqual(self.lifecycle.profile('alice@example.com'), {'display_name': 'Alice', 'language': 'fr'})
        with self.assertRaises(ValueError):
            self.lifecycle.update('alice@example.com', {'display_name': 'Alice', 'language': 'fr', 'role': 'admin'})
        self.assertEqual(self.accounts.list_users()[0]['role'], 'viewer')

    def test_password_change_revokes_every_session_and_token(self):
        session = self.accounts.login('alice@example.com', PASSWORD, 'fixture')
        token = self.accounts.service.issue_token('alice@example.com', ['status:read'], 60)
        self.lifecycle.change_password('alice@example.com', PASSWORD, NEW_PASSWORD, 'fixture')
        for resolve, secret in ((self.accounts.service.authenticate_session, session.secret),
                                (self.accounts.service.authenticate_token, token.secret)):
            with self.assertRaises(AuthenticationDenied):
                resolve(secret)
        self.assertEqual(self.accounts.login('alice@example.com', NEW_PASSWORD, 'fixture').identity.principal, 'alice@example.com')

    def test_wrong_current_password_preserves_account(self):
        with self.assertRaises(AuthenticationDenied):
            self.lifecycle.change_password('alice@example.com', 'wrong-password-123', NEW_PASSWORD, 'fixture')
        self.accounts.login('alice@example.com', PASSWORD, 'fixture')

    def test_recovery_is_hashed_single_use_and_preserves_role(self):
        code = self.lifecycle.issue_recovery('alice@example.com')
        with self.accounts.store.transaction() as tx:
            record = tx.get('accounts', 'alice@example.com')
        self.assertNotIn(code, str(record))
        self.assertEqual(record['recovery']['digest'], hashlib.sha256(code.encode()).hexdigest())
        self.lifecycle.recover('alice@example.com', code, NEW_PASSWORD, 'fixture')
        with self.assertRaises(AuthenticationDenied):
            self.lifecycle.recover('alice@example.com', code, PASSWORD, 'fixture')
        self.assertEqual(self.accounts.list_users()[0]['role'], 'viewer')

    def test_expired_rotated_and_disabled_recovery_fail(self):
        old = self.lifecycle.issue_recovery('alice@example.com')
        current = self.lifecycle.issue_recovery('alice@example.com')
        with self.assertRaises(AuthenticationDenied):
            self.lifecycle.recover('alice@example.com', old, NEW_PASSWORD, 'fixture')
        self.now[0] += 901
        with self.assertRaises(AuthenticationDenied):
            self.lifecycle.recover('alice@example.com', current, NEW_PASSWORD, 'fixture')
        current = self.lifecycle.issue_recovery('alice@example.com')
        self.accounts.service.disable('alice@example.com')
        with self.assertRaises(AuthenticationDenied):
            self.lifecycle.recover('alice@example.com', current, NEW_PASSWORD, 'fixture')

    def test_concurrent_admin_reset_cannot_be_overwritten(self):
        original = self.accounts.passwords.hash
        def race(password):
            encoded = original(password)
            with self.accounts.store.transaction() as tx:
                account = tx.get('accounts', 'alice@example.com')
                account['revision'] = 'concurrent-reset'
                tx.put('accounts', 'alice@example.com', account)
            return encoded
        with patch.object(self.accounts.passwords, 'hash', side_effect=race), self.assertRaises(AuthenticationDenied):
            self.lifecycle.change_password('alice@example.com', PASSWORD, NEW_PASSWORD, 'fixture')

    def test_profile_and_password_bounds(self):
        for name, language in [('x\n', 'fr'), ('x' * 81, 'fr'), ('Alice', 'invalid'), ('', 'en')]:
            with self.assertRaises(ValueError):
                self.lifecycle.update('alice@example.com', {'display_name': name, 'language': language})
        for password in ['short', '\0' * 12, 'é' * 513]:
            with self.assertRaises(ValueError):
                self.lifecycle.change_password('alice@example.com', PASSWORD, password, 'fixture')
