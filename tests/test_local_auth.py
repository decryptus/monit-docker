import hashlib
from pathlib import Path
import tempfile
import unittest

from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable
from monit_docker.adapters.local_auth import Accounts
from monit_docker.auth_policy import username, email_identifier, role_scopes

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
        self.accounts.provision('alice@example.com', PASSWORD, 'operator')

    def open(self, create=False):
        account = Accounts(self.path, create=create, clock=lambda: self.now[0], passwords=FastPasswords())
        self.addCleanup(account.close)
        return account

    def login(self):
        return self.accounts.login('alice@example.com', PASSWORD, 'local-fixture')

    def test_sessions_survive_reopening_and_store_only_digests(self):
        grant = self.login()
        second = self.open()
        self.assertEqual(second.service.authenticate_session(grant.secret).principal, 'alice@example.com')
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
        token = self.accounts.service.issue_token('alice@example.com', ['status:read'], 100)
        self.open().set_role('alice@example.com', 'viewer')
        for call, secret in [(self.accounts.service.authenticate_session, grant.secret),
                             (self.accounts.service.authenticate_token, token.secret)]:
            with self.assertRaises(AuthenticationDenied):
                call(secret)
        self.assertNotIn('actions:write', self.login().identity.scopes)

    def test_disable_and_password_reset_invalidate_access(self):
        grant = self.login()
        self.accounts.service.disable('alice@example.com')
        with self.assertRaises(AuthenticationDenied):
            self.login()
        self.accounts.provision('alice@example.com', 'new-test-password-123', 'operator')
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
        self.assertEqual(self.accounts.service.authenticate_session(grant.secret, grant.csrf, True).principal, 'alice@example.com')

    def test_accounts_cannot_revoke_another_users_session(self):
        grant = self.login()
        credential = self.accounts.sessions('alice@example.com')[0]['id']
        with self.assertRaises(AuthenticationDenied):
            self.accounts.revoke('bob@example.com', credential)
        self.accounts.revoke('alice@example.com', credential)
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(grant.secret)

    def test_token_scopes_cannot_exceed_account(self):
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.issue_token('alice@example.com', ['auth:manage'], 100)

    def test_no_memory_fallback_when_database_is_replaced(self):
        grant = self.login()
        self.path.rename(self.path.with_suffix('.saved'))
        self.path.touch(mode=0o600)
        with self.assertRaises(AuthenticationUnavailable):
            self.accounts.service.authenticate_session(grant.secret)

    def test_login_throttling_survives_restart(self):
        for _ in range(5):
            with self.assertRaises(AuthenticationDenied):
                self.accounts.login('alice@example.com', 'wrong-password-123', 'local-fixture')
        with self.assertRaises(AuthenticationDenied):
            self.open().login('alice@example.com', PASSWORD, 'other-fixture')

    def test_identifier_boundaries(self):
        for value in ('alice\n', 'é', '', 'a' * 65, 'alice:admin', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                username(value)

    def test_roles_are_explicit_and_unknown_roles_never_expand(self):
        with self.assertRaises(ValueError):
            self.accounts.provision('bob@example.com', PASSWORD, 'unknown')
        self.assertEqual(len(self.accounts.list_users()), 1)

    def test_new_accounts_require_email_without_creating_rejected_records(self):
        for principal in ('new-user', 'alice\n@example.com', 'é@example.com',
                          'alice@localhost', 'a..b@example.com', 'a@-example.com',
                          'a@example.com\n', 'a@example.com\x00', 'a@exam_ple.com',
                          'a' * 65 + '@example.com', 'a@' + 'b' * 250 + '.com',
                          'Alice <alice@example.com>', '"alice"@example.com'):
            with self.subTest(principal=principal), self.assertRaises(ValueError):
                self.accounts.provision(principal, PASSWORD, 'viewer')
        self.assertEqual(len(self.accounts.list_users()), 1)

    def test_email_identity_preserves_exact_case_and_survives_reopen(self):
        principal = 'Ops.Alerts+docker@Example.com'
        self.accounts.provision(principal, PASSWORD, 'viewer')
        grant = self.open().login(principal, PASSWORD, 'fixture')
        self.assertEqual(self.accounts.service.authenticate_session(grant.secret).principal, principal)
        with self.assertRaises(AuthenticationDenied):
            self.accounts.login(principal.lower(), PASSWORD, 'fixture')

    def test_email_identifier_length_and_full_match_boundaries(self):
        longest = 'a' * 64 + '@' + 'b' * 63 + '.' + 'c' * 63 + '.' + 'd' * 61
        self.assertEqual(len(longest), 254)
        self.assertTrue(email_identifier(longest))
        self.assertFalse(email_identifier(longest + 'e'))
        for value in (None, 42, 'a@example.com\r', 'a@example.com\u2028', 'a@例.com'):
            with self.subTest(value=value):
                self.assertFalse(email_identifier(value))

    def test_existing_legacy_account_can_login_reset_and_keep_role_controls(self):
        # Seed the pre-upgrade database through the existing shared backend.
        self.accounts.service.provision('legacy-user', PASSWORD, role_scopes('operator'))
        old = self.accounts.login('legacy-user', PASSWORD, 'fixture')
        self.accounts.provision('legacy-user', PASSWORD, 'viewer')
        with self.assertRaises(AuthenticationDenied):
            self.accounts.service.authenticate_session(old.secret)
        self.assertEqual(self.accounts.login('legacy-user', PASSWORD, 'fixture').identity.scopes, {'status:read'})
        self.accounts.set_role('legacy-user', 'operator')
        self.assertIn('actions:write', self.accounts.login('legacy-user', PASSWORD, 'fixture').identity.scopes)

    def test_startup_warns_once_with_count_without_account_values(self):
        from monit_docker.composition import build_authentication
        self.accounts.service.provision('private-legacy-one', PASSWORD, role_scopes('viewer'))
        self.accounts.service.provision('private-legacy-two', PASSWORD, role_scopes('viewer'))
        self.accounts.service.disable('private-legacy-two')
        with self.assertLogs('monit-docker', level='WARNING') as captured:
            transport = build_authentication(self.path, 'https://monitor.example')
            try:
                transport.accounts.list_users()
                transport.accounts.list_users()
            finally:
                transport.accounts.close()
        self.assertEqual(len(captured.output), 1)
        self.assertIn('auth_accounts_without_email: 2', captured.output[0])
        self.assertNotIn('private-legacy', captured.output[0])
        self.assertNotIn(PASSWORD, captured.output[0])

    def test_startup_with_email_accounts_has_no_missing_email_warning(self):
        from monit_docker.composition import build_authentication
        with self.assertNoLogs('monit-docker', level='WARNING'):
            transport = build_authentication(self.path, 'https://monitor.example')
            transport.accounts.close()
