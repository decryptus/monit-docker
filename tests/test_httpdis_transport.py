"""Wire-level regressions for HTTPdis routing and Sonicprobe worker lifecycle."""

import http.client
import json
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.http import StatusHandler, StatusServer
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService
from test_manual_actions import ORIGIN, TOKEN, payload, snapshot


_ADDRESS = ('127.0.0.1', 0)
_HEADERS = {'Content-Type': 'application/json',
            'Origin': ORIGIN,
            'X-Monit-Action-Token': TOKEN}


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.actions = ManualActions(Mock(), ORIGIN, TOKEN)
        self.monitor = MonitorService(Mock(return_value=snapshot()), manual_actions=self.actions)
        self.monitor.run_cycle()
        self.server = self.start_server(self.monitor)

    def start_server(self, monitor):
        server = StatusServer(_ADDRESS, monitor)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(self.close_server, server, thread)
        return server

    def close_server(self, server, thread):
        server.shutdown()
        thread.join(2)
        server.server_close()
        self.assertFalse(thread.is_alive())

    def request(self, path, method='GET', server=None):
        connection = http.client.HTTPConnection(*(server or self.server).server_address, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def raw_post(self, extra=(), body=None, declared_length=None):
        body = json.dumps(payload()).encode() if body is None else body
        connection = socket.create_connection(self.server.server_address, timeout=2)
        self.addCleanup(connection.close)
        headers = list(_HEADERS.items())
        headers.append(('Content-Length', str(len(body) if declared_length is None else declared_length)))
        headers.extend(extra)
        request = 'POST /v1/actions HTTP/1.0\r\n' + ''.join('%s: %s\r\n' % h for h in headers)
        connection.sendall(request.encode() + b'\r\n' + body)
        if declared_length is not None:
            connection.shutdown(socket.SHUT_WR)
        response = http.client.HTTPResponse(connection)
        response.begin()
        return response.status, json.loads(response.read())

    def test_duplicate_credentials_and_lengths_never_queue_actions(self):
        for header, value, expected in (
                ('Origin', ORIGIN, 403),
                ('X-Monit-Action-Token', TOKEN, 403),
                ('Content-Length', '1', 400),
                ('Transfer-Encoding', 'identity', 400)):
            with self.subTest(header=header):
                self.assertEqual(self.raw_post(((header, value),))[0], expected)
        self.assertEqual(self.actions.status()['recent'], [])
        self.actions.execute.assert_not_called()

    def test_truncated_and_invalid_utf8_bodies_are_rejected_without_parser_details(self):
        for body, length in ((b'{', 20), (b'\xff', None)):
            code, data = self.raw_post(body=body, declared_length=length)
            self.assertEqual(code, 400)
            self.assertIn(data['error'], ('invalid_request', 'invalid_json'))
        self.assertEqual(self.actions.status()['recent'], [])

    def test_head_matches_get_headers_and_no_cors_is_enabled(self):
        for path in ('/healthz', '/readyz', '/v1/status', '/metrics', '/missing'):
            get_code, get_headers, body = self.request(path)
            head_code, head_headers, head_body = self.request(path, 'HEAD')
            self.assertEqual(head_code, get_code)
            self.assertEqual(head_body, b'')
            self.assertEqual(head_headers['Content-Type'], get_headers['Content-Type'])
            # The age in status/metrics changes between requests; stable payloads match exactly.
            if path in ('/healthz', '/readyz', '/missing'):
                self.assertEqual(int(head_headers['Content-Length']), len(body))
            self.assertEqual(head_headers['Cache-Control'], 'no-store')
        code, headers, _ = self.request('/v1/actions', 'OPTIONS')
        self.assertEqual(code, 405)
        self.assertEqual(headers['Allow'], 'POST')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_multiple_servers_do_not_share_monitor_or_action_credentials(self):
        other = MonitorService(Mock(return_value=snapshot()))
        second = self.start_server(other)
        for server, ready, actions in ((self.server, True, True), (second, False, False)):
            code, _, body = self.request('/v1/status', server=server)
            data = json.loads(body)
            self.assertEqual((code, data['ready'], data['manual_actions']['enabled']), (200, ready, actions))
        self.assertEqual(self.raw_post()[0], 202)
        self.assertFalse(other.status()['manual_actions']['enabled'])

    def test_incomplete_body_times_out_and_worker_can_serve_next_request(self):
        with patch.object(StatusHandler, 'timeout', 0.1):
            connection = socket.create_connection(self.server.server_address, timeout=2)
            try:
                headers = ''.join('%s: %s\r\n' % h for h in _HEADERS.items())
                connection.sendall(('POST /v1/actions HTTP/1.0\r\n' + headers +
                                    'Content-Length: 100\r\n\r\n{').encode())
                response = http.client.HTTPResponse(connection)
                response.begin()
                self.assertEqual(response.status, 408)
                self.assertEqual(json.loads(response.read())['error'], 'request_timeout')
            finally:
                connection.close()
        self.assertEqual(self.request('/healthz')[0], 200)
        self.assertEqual(self.actions.status()['recent'], [])

    def test_saturated_pool_shutdown_is_bounded_and_workers_exit(self):
        # Fill all workers and their bounded pending queue with idle sockets.
        baseline = set(threading.enumerate())
        release = threading.Event()
        def hold_request(handler):
            release.wait(5)
        with patch.object(StatusHandler, 'handle', hold_request):
            server = self.start_server(MonitorService(Mock(return_value=snapshot())))
            workers = [thread for thread in threading.enumerate()
                       if thread not in baseline and thread.name.startswith('monit-docker-http:')]
            self.assertEqual(len(workers), 8)
            sockets = []
            try:
                for _ in range(17):
                    connection = socket.create_connection(server.server_address, timeout=2)
                    connection.sendall(b'GET /healthz HTTP/1.0\r\n')
                    sockets.append(connection)
                deadline = time.monotonic() + 2
                while server.requests.qsize() != 8 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(server.requests.qsize(), 8)
                start = time.monotonic()
                server.shutdown()
                server.server_close()
                self.assertEqual(server.requests.qsize(), 0)
                release.set()
                for worker in workers:
                    worker.join(2)
                    self.assertFalse(worker.is_alive())
                self.assertLess(time.monotonic() - start, 2)
                self.assertLessEqual(server.requests.qsize(), 8)
            finally:
                release.set()
                for connection in sockets:
                    connection.close()
