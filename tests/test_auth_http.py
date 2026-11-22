import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from monit_docker.adapters.http import StatusServer
from monit_docker.adapters.auth_http import AuthTransport
from monit_docker.adapters.local_auth import Accounts
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService
from test_manual_actions import snapshot, payload
from test_local_auth import FastPasswords, PASSWORD

ORIGIN = 'https://monitor.example'

class AuthHttpTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.accounts = Accounts(Path(self.directory.name) / 'auth.db', create=True, passwords=FastPasswords())
        self.addCleanup(self.accounts.close)
        for name, role in [('reader@example.com', 'viewer'), ('operator@example.com', 'operator'), ('admin@example.com', 'admin')]:
            self.accounts.provision(name, PASSWORD, role)
        self.actions = ManualActions(Mock())
        self.monitor = MonitorService(Mock(return_value=snapshot()), manual_actions=self.actions)
        self.monitor.run_cycle()
        self.server = StatusServer(('127.0.0.1', 0), self.monitor, auth=AuthTransport(self.accounts, ORIGIN))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        values = {'Host': 'monitor.example'}
        values.update(headers or {})
        if body is not None:
            values['Content-Type'] = 'application/json'
            body = json.dumps(body)
        try:
            connection.request(method or ('POST' if body is not None else 'GET'), path, body, values)
            response = connection.getresponse()
            raw = response.read()
            return response.status, {k.lower(): v for k, v in response.getheaders()}, json.loads(raw) if raw else None
        finally:
            connection.close()

    def login(self, user='reader@example.com'):
        status, _, body = self.request('/v1/auth/login', {'username': user, 'password': PASSWORD}, {'X-Monit-Client': 'native-v1'})
        self.assertEqual(status, 200, body)
        return {'Authorization': 'Session ' + body['session_token'], 'X-CSRF-Token': body['csrf_token']}

    def test_public_status_requires_session_in_sqlite_mode(self):
        code, headers, _ = self.request('/v1/status')
        self.assertEqual(code, 401)
        self.assertEqual(headers['www-authenticate'], 'Session')
        self.assertEqual(self.request('/v1/auth/info')[2]['native_auth'], 'session-v1')

    def test_native_login_read_and_server_logout(self):
        headers = self.login()
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 200)
        self.assertEqual(self.request('/v1/auth/logout', {}, headers)[0], 200)
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 401)

    def test_unspecified_push_option_exposes_false_not_null(self):
        self.server.auth = AuthTransport(self.accounts, ORIGIN, mobile_push=None)
        self.assertIs(self.request('/v1/auth/info')[2]['mobile_push'], False)
        headers = self.login()
        self.assertIs(self.request('/v1/auth/devices', headers=headers)[2]['enabled'], False)

    def test_reader_cannot_execute_or_create_admin(self):
        headers = self.login()
        self.assertEqual(self.request('/v1/actions', payload(), headers)[0], 403)
        self.assertEqual(self.request('/v1/auth/users', {'operation': 'role', 'username': 'reader@example.com', 'role': 'admin'}, headers)[0], 403)
        self.assertFalse(self.request('/v1/status', headers=headers)[2]['manual_actions']['enabled'])
        self.actions.execute.assert_not_called()

    def test_http_provision_requires_email_and_new_account_can_login(self):
        headers = self.login('admin@example.com')
        body = {'operation': 'provision', 'username': 'new-legacy', 'password': PASSWORD, 'role': 'viewer'}
        self.assertEqual(self.request('/v1/auth/users', body, headers)[0], 400)
        self.assertNotIn('new-legacy', [user['username'] for user in self.accounts.list_users()])
        body['username'] = 'new+mobile@example.com'
        self.assertEqual(self.request('/v1/auth/users', body, headers)[0], 200)
        native = self.login(body['username'])
        self.assertEqual(self.request('/v1/auth/me', headers=native)[2]['username'], body['username'])

    def test_http_legacy_account_remains_usable_after_upgrade(self):
        from monit_docker.auth_policy import role_scopes
        self.accounts.service.provision('legacy-viewer', PASSWORD, role_scopes('viewer'))
        headers = self.login('legacy-viewer')
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 200)
        self.assertEqual(self.request('/v1/auth/profile', headers=headers)[0], 200)

    def test_operator_action_uses_authenticated_actor_not_proxy_header(self):
        headers = self.login('operator@example.com')
        headers['X-Monit-Actor'] = 'spoofed-admin'
        code, _, body = self.request('/v1/actions', payload(), headers)
        self.assertEqual(code, 202, body)
        self.assertEqual(body['actor'], 'operator@example.com')

    def test_no_fallback_to_old_proxy_secrets(self):
        code, _, _ = self.request('/v1/actions', payload(), {'X-Monit-Action-Token': 'a' * 64, 'Origin': ORIGIN})
        self.assertEqual(code, 401)

    def test_browser_cookie_secure_and_csrf_required(self):
        code, headers, body = self.request('/v1/auth/login', {'username': 'operator@example.com', 'password': PASSWORD}, {'Origin': ORIGIN})
        self.assertEqual(code, 200)
        self.assertNotIn('session_token', body)
        cookie = headers['set-cookie']
        for part in ('__Host-monit-session=', 'HttpOnly', 'Secure', 'SameSite=Strict', 'Path=/'):
            self.assertIn(part, cookie)
        browser = {'Cookie': cookie.split(';')[0], 'Origin': ORIGIN}
        self.assertEqual(self.request('/v1/status', headers=browser)[0], 200)
        self.assertEqual(self.request('/v1/actions', payload(), browser)[0], 401)
        browser['X-CSRF-Token'] = body['csrf_token']
        self.assertEqual(self.request('/v1/auth/logout', {}, browser)[0], 200)
        self.assertEqual(self.request('/v1/status', headers=browser)[0], 401)

    def test_foreign_origin_and_mixed_credentials_rejected(self):
        for extra in ({'Origin': 'https://evil.example'}, {'Cookie': 'anything=x'}, {'Sec-Fetch-Site': 'cross-site'}):
            code, _, _ = self.request('/v1/auth/login', {'username': 'reader@example.com', 'password': PASSWORD}, {'X-Monit-Client': 'native-v1', **extra})
            self.assertEqual(code, 401)
        headers = self.login()
        headers['Cookie'] = 'anything=x'
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 401)
        self.assertEqual(self.request('/v1/auth/login', method='OPTIONS')[0], 405)

    def test_admin_role_change_revokes_existing_session(self):
        reader, admin = self.login(), self.login('admin@example.com')
        self.assertEqual(self.request('/v1/auth/users', {'operation': 'role', 'username': 'reader@example.com', 'role': 'operator'}, admin)[0], 200)
        self.assertEqual(self.request('/v1/status', headers=reader)[0], 401)

    def test_session_listing_and_individual_revocation(self):
        headers = self.login()
        sessions = self.request('/v1/auth/sessions', headers=headers)[2]['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertEqual(self.request('/v1/auth/sessions', {'id': sessions[0]['id']}, headers)[0], 200)
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 401)

    def test_store_failure_returns_503_without_anonymous_fallback(self):
        headers = self.login()
        self.accounts.close()
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 503)

    def test_api_token_has_explicit_scope_without_session_cookie(self):
        grant = self.accounts.service.issue_token('operator@example.com', ['status:read'], 60)
        headers = {'Authorization': 'Bearer ' + grant.secret}
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 200)
        self.assertEqual(self.request('/v1/actions', payload(), headers)[0], 403)

    def test_unknown_path_and_oversized_login_are_bounded(self):
        self.assertEqual(self.request('/v1/auth/login/', {})[0], 404)
        code, headers, body = self.request('/v1/auth/login', {'username': 'reader@example.com', 'password': 'x' * 5000}, {'X-Monit-Client': 'native-v1'})
        self.assertEqual(code, 413)
        self.assertEqual(headers['cache-control'], 'no-store')
        self.assertNotIn('access-control-allow-origin', headers)

    def test_account_profile_session_only_and_csrf(self):
        headers = self.login()
        profile = {'display_name': 'Reader phone', 'language': 'fr'}
        self.assertEqual(self.request('/v1/auth/profile', profile, headers)[0], 200)
        self.assertEqual(self.request('/v1/auth/profile', headers=headers)[2], profile)
        self.assertEqual(self.request('/v1/auth/profile', profile, {'Authorization': headers['Authorization']})[0], 401)
        token = self.accounts.service.issue_token('reader@example.com', ['status:read'], 60)
        self.assertEqual(self.request('/v1/auth/profile', headers={'Authorization': 'Bearer ' + token.secret})[0], 401)

    def test_password_change_and_recovery_http(self):
        headers = self.login()
        new = 'new-password-123456'
        self.assertEqual(self.request('/v1/auth/password', {'current_password': PASSWORD, 'new_password': new}, headers)[0], 200)
        self.assertEqual(self.request('/v1/status', headers=headers)[0], 401)
        code = self.server.auth.lifecycle.issue_recovery('reader@example.com')
        body = {'username': 'reader@example.com', 'recovery_code': code, 'new_password': PASSWORD}
        self.assertEqual(self.request('/v1/auth/recover', body, {'X-Monit-Client': 'native-v1', 'Origin': ORIGIN})[0], 401)
        self.assertEqual(self.request('/v1/auth/recover', body, {'X-Monit-Client': 'native-v1'})[0], 200)
        self.assertEqual(self.request('/v1/auth/recover', body, {'X-Monit-Client': 'native-v1'})[0], 401)
        self.assertTrue(self.login())

    def test_push_is_opt_in_session_bound_and_logout_revokes(self):
        headers = self.login()
        device = {'token': 'ExpoPushToken[fixture-token-123]', 'language': 'en', 'channel': 'monit-observations-1-1'}
        self.assertFalse(self.request('/v1/auth/devices', headers=headers)[2]['enabled'])
        self.assertEqual(self.request('/v1/auth/devices', device, headers)[0], 409)
        self.server.auth.mobile_push = True
        self.assertEqual(self.request('/v1/auth/devices', device, headers)[0], 200)
        self.assertEqual(len(self.server.auth.devices.pending()), 1)
        self.assertEqual(self.request('/v1/auth/logout', {}, headers)[0], 200)
        self.assertEqual(self.server.auth.devices.pending(), [])
