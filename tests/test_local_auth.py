import hashlib
from pathlib import Path
import tempfile
import unittest

from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable
from monit_docker.adapters.local_auth import Accounts
from monit_docker.auth_policy import username

PASSWORD = 'test-only-password-123'

class FastPasswords:
    def hash(self, password):
        if len(password) < 12:
            raise ValueError('short password')
        return hashlib.sha256(password.encode()).hexdigest()
    def verify(self, encoded, password):
        return encoded == self.hash(password)

class LocalAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'auth.db'
        self.now = [1000.0]
        self.accounts = self.open(create=True)
        self.accounts.provision('alice', PASSWORD, 'operator')

    def open(self, create=False):
        account = Accounts(self.path, create=create, clock=lambda: self.now[0], passwords=FastPasswords())
        self.addCleanup(account.close)
        return account

    def login(self):
        return self.accounts.login('alice', PASSWORD, 'local-fixture')

    def test_sessions_survive_reopening_and_store_only_digests(self):
        grant = self.login()
        second = self.open()
        self.assertEqual(second.service.authenticate_session(grant.secret).principal, 'alice')
        content = self.path.read_bytes()
        for secret in (grant.secret, grant.csrf, PASSWORD):
            self.assertNotIn(secret.encode(), content)

    def test_logout_revokes_across_instances(self):
        grant = self.login()
        second = self.open()
        second.service.logout(grant.secret)
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)

    def test_role_change_revokes_sessions_and_tokens(self):
        grant = self.login()
        token = self.accounts.service.issue_token('alice', ['status:read'], 100)
        self.open().set_role('alice', 'viewer')
        for call, secret in [(self.accounts.service.authenticate_session, grant.secret),
                             (self.accounts.service.authenticate_token, token.secret)]:
            with self.assertRaises(AuthenticationDenied):
                call(secret)
        self.assertNotIn('actions:write', self.login().identity.scopes)

    def test_disable_and_password_reset_invalidate_access(self):
        grant = self.login()
        self.accounts.service.disable('alice')
        with self.assertRaises(AuthenticationDenied):
            self.login()
        self.accounts.provision('alice', 'new-test-password-123', 'operator')
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)

    def test_idle_and_absolute_expiry(self):
        grant = self.login()
        self.now[0] += 1801
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)
        grant = self.login()
        for _ in range(28):
            self.now[0] += 1000
            self.accounts.service.authenticate_session(grant.secret)
        self.now[0] += 801
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)

    def test_mutations_require_matching_csrf(self):
        grant = self.login()
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret, 'wrong', mutation=True)
        self.assertEqual(self.accounts.service.authenticate_session(grant.secret, grant.csrf, True).principal, 'alice')

    def test_accounts_cannot_revoke_another_users_session(self):
        grant = self.login()
        credential = self.accounts.sessions('alice')[0]['id']
        with self.assertRaises(AuthenticationDenied):
            self.accounts.revoke('bob', credential)
        self.accounts.revoke('alice', credential)
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)

    def test_token_scopes_cannot_exceed_account(self):
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.issue_token('alice', ['auth:manage'], 100)

    def test_no_memory_fallback_when_database_is_replaced(self):
        grant = self.login()
        self.path.rename(self.path.with_suffix('.saved'))
        self.path.touch(mode=0o600)
        with self.assertRaises(AuthenticationUnavailable):
            self.accounts.service.authenticate_session(grant.secret)

    def test_login_throttling_survives_restart(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationDenied):
                self.accounts.login('alice', 'wrong-password-123', 'local-fixture')
        with self.assertRaises(AuthenticationDenied):
            self.open().login('alice', PASSWORD, 'other-fixture')

    def test_identifier_boundaries(self):
        for value in ('alice\n', 'é', '', 'a' * 65, 'alice:admin', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                username(value)

    def test_roles_are_explicit_and_unknown_roles_never_expand(self):
        with self.assertRaises(ValueError):
            self.accounts.provision('bob', PASSWORD, 'unknown')
        self.assertEqual(len(self.accounts.list_users()), 1)
