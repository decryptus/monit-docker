"""Real HTTPdis transport, separate spending permission and bounded requests."""
import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.http import StatusServer
from monit_docker.adapters.http_security import HttpSecurity, load_security
from monit_docker.domain.errors import MonitoringError
from monit_docker.incident_session import IncidentSessions
from monit_docker.service import MonitorService
from incident_fixtures import status, analysis, IDENTIFIER, NOW

TOKEN = 'f' * 64
ORIGIN = 'https://monitor.example'
PATH = '/v1/ai/preview'
HEADERS = {'Content-Type': 'application/json', 'Origin': ORIGIN, 'X-Monit-AI-Token': TOKEN,
           'X-Monit-Actor': 'alice'}


class IncidentHTTPTests(unittest.TestCase):
    def setUp(self):
        self.provider = Mock()
        self.provider.complete.return_value = dict(analysis=analysis(), usage={})
        self.assistant = IncidentSessions(lambda: self.provider, clock=lambda: NOW)
        self.monitor = MonitorService(Mock(), incident_assistant=self.assistant)
        self.monitor.status = Mock(side_effect=lambda: status())
        self.server = StatusServer(('127.0.0.1', 0), self.monitor,
            HttpSecurity(ai_token=TOKEN, ai_origin=ORIGIN, trust_actor=True))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, path=PATH, payload=None, headers=None, method='POST'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, json.dumps(payload or {'container_id': IDENTIFIER}),
                               HEADERS if headers is None else dict({'Content-Type': 'application/json'}, **headers))
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_preview_then_explain_without_action_permission(self):
        code, preview = self.request()
        self.assertEqual(code, 200)
        self.provider.complete.assert_not_called()
        code, result = self.request('/v1/ai/explain', dict(container_id=IDENTIFIER, preview_id=preview['preview_id']))
        self.assertEqual((code, result['status']), (200, 'completed'))
        self.assertIsNone(self.monitor.manual_actions)
        self.assertEqual(self.provider.complete.call_args.args[1], preview['context'])

    def test_read_action_credentials_wrong_origin_and_missing_actor_are_rejected(self):
        for headers in ({}, {'X-Monit-Action-Token': TOKEN}, {'Authorization': 'Bearer ' + TOKEN},
                        dict(HEADERS, Origin='https://other.example'), dict(HEADERS, **{'X-Monit-Actor': ''})):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request(headers=headers)[0], 403)
        self.provider.complete.assert_not_called()

    def test_disabled_routes_unknown_fields_body_size_and_method(self):
        self.assertEqual(self.request(payload=dict(container_id=IDENTIFIER, endpoint='https://evil.example'))[0], 400)
        self.assertEqual(self.request(payload=dict(container_id='a' * 2000))[0], 413)
        self.assertEqual(self.request(method='GET')[0], 405)
        self.monitor.incident_assistant = None
        self.assertEqual(self.request()[0], 404)
        self.provider.complete.assert_not_called()

    def test_preview_cannot_be_spent_by_another_actor_or_reused(self):
        _, preview = self.request()
        payload = dict(container_id=IDENTIFIER, preview_id=preview['preview_id'])
        self.assertEqual(self.request('/v1/ai/explain', payload, dict(HEADERS, **{'X-Monit-Actor': 'bob'}))[0], 409)
        self.assertEqual(self.request('/v1/ai/explain', payload)[0], 200)
        self.assertEqual(self.request('/v1/ai/explain', payload)[0], 409)
        self.provider.complete.assert_called_once()

    def test_ai_secret_cannot_reuse_any_existing_role(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'token'
            path.write_text(TOKEN)
            for field in ('action_token_file', 'notification_token_file', 'audit_read_token_file',
                          'metrics_token_file', 'status_token_file'):
                with self.assertRaises(MonitoringError):
                    load_security(ai_token_file=path, **{field: path})

    def test_invalid_provider_does_not_prevent_server_or_preview(self):
        from monit_docker.composition import build_incident_assistant
        self.monitor.incident_assistant = build_incident_assistant('/does-not-exist/ai.yml')
        self.monitor.incident_assistant.clock = lambda: NOW
        _, preview = self.request()
        code, result = self.request('/v1/ai/explain', dict(container_id=IDENTIFIER, preview_id=preview['preview_id']))
        self.assertEqual(code, 503)
        self.assertEqual(result['error'], 'invalid_provider_configuration')
        self.assertEqual(self.request('/v1/status', method='GET')[0], 200)
