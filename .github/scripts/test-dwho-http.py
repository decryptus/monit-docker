"""Run the shipped CLI/config/template through real DWho against a local HTTP sink."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'examples/monitoring/dwho-http'


class HttpExampleTests(unittest.TestCase):
    def setUp(self):
        self.messages = queue.Queue()
        self.status = 202
        self.delay = 0
        fixture = self

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                fixture.messages.put((self.path, self.headers.get('Authorization'),
                                      self.headers.get('Content-Type'),
                                      json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                time.sleep(fixture.delay)
                self.send_response(fixture.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Sink)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Path(self.temp.name)
        (self.config / 'http.yml').write_text((EXAMPLE / 'http.yml').read_text().replace(
            'https://example.invalid/notifications', 'http://127.0.0.1:%s/notifications' % self.server.server_port))
        (self.config / 'http.json').write_text((EXAMPLE / 'http.json').read_text())
        # Quotes/backslashes exercise JSON-safe template rendering.
        self.token = 'test-token-"quoted"-\\value'
        (self.config / 'token').write_text(self.token)
        self.payload = json.loads((EXAMPLE / 'payload.json').read_text())

    def send(self, payload=None):
        return subprocess.run([sys.executable, str(EXAMPLE / 'send.py'), '--config-dir', str(self.config),
                               '--token-file', str(self.config / 'token')],
                              input=json.dumps(self.payload if payload is None else payload),
                              text=True, capture_output=True, timeout=10)

    def test_full_payload_and_bearer_for_firing_and_resolved(self):
        for status in ('firing', 'resolved'):
            self.payload['status'] = self.payload['alerts'][0]['status'] = status
            self.payload['alerts'][0]['annotations']['summary'] = 'Mémoire "haute"\nline two'
            result = self.send()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.messages.get(timeout=1),
                             ('/notifications', 'Bearer ' + self.token, 'application/json', self.payload))

    def test_http_errors_propagate_without_disclosing_token(self):
        for status in (401, 429, 500):
            self.status = status
            result = self.send()
            self.assertEqual(result.returncode, 1)
            self.assertIn('HTTPError', result.stderr)
            self.assertNotIn(self.token, result.stderr + result.stdout)

    def test_timeout_is_a_failure(self):
        p = self.config / 'http.json'
        p.write_text(p.read_text().replace('"timeout": 5', '"timeout": 0.1'))
        self.delay = 0.5
        result = self.send()
        self.assertEqual(result.returncode, 1)
        self.assertIn('Timeout', result.stderr)

    def test_missing_template_or_invalid_payload_sends_nothing(self):
        self.assertEqual(self.send([]).returncode, 1)
        (self.config / 'http.json').unlink()
        self.assertEqual(self.send().returncode, 1)
        self.assertTrue(self.messages.empty())


if __name__ == '__main__':
    unittest.main()
