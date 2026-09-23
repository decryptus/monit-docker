"""Bounded local read-only HTTP interface over a cached monitoring service."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import hmac
import logging
import signal
from socketserver import ThreadingMixIn
from threading import BoundedSemaphore, Event, Thread, current_thread, main_thread
from urllib.parse import urlsplit

from monit_docker.outputs.prometheus import render_metrics
from monit_docker.domain.errors import ActionRejected

LOG = logging.getLogger('monit-docker')


class StatusHandler(BaseHTTPRequestHandler):
    server_version = 'monit-docker'
    sys_version = ''

    def _send(self, code, value, content_type='application/json; charset=utf-8'):
        body = (json.dumps(value, allow_nan=False) + '\n' if isinstance(value, dict) else value).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if code == 405:
            self.send_header('Allow', 'POST' if self.server.monitor.manual_actions is not None
                             and urlsplit(self.path).path == '/v1/actions' else 'GET, HEAD')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            return self._send(200, {'alive': True})
        if path not in ('/readyz', '/v1/status', '/metrics'):
            return self._send(404, {'error': 'not_found'})
        data = self.server.monitor.status()
        if path == '/readyz':
            return self._send(200 if data['ready'] else 503, {'ready': data['ready']})
        if path == '/metrics':
            return self._send(200, render_metrics(data), 'text/plain; version=0.0.4; charset=utf-8')
        self._send(200, data)

    do_HEAD = do_GET

    def do_POST(self):
        actions = self.server.monitor.manual_actions
        if urlsplit(self.path).path != '/v1/actions' or actions is None:
            return self._method_not_allowed()
        # Ignore forwarded identity headers: only the configured proxy secret
        # authorizes writes. Require a fixed browser origin to prevent CSRF.
        token = self.headers.get_all('X-Monit-Action-Token', [])
        origin = self.headers.get_all('Origin', [])
        if (len(token) != 1 or not hmac.compare_digest(
                token[0].encode('utf-8'), actions.token.encode('ascii'))):
            return self._send(403, {'error': 'forbidden'})
        if origin != [actions.origin]:
            return self._send(403, {'error': 'origin_rejected'})
        if self.headers.get('Content-Type', '').lower() != 'application/json':
            return self._send(415, {'error': 'json_required'})
        lengths = self.headers.get_all('Content-Length', [])
        if (self.headers.get('Transfer-Encoding') is not None or len(lengths) != 1
                or len(lengths[0]) > 4 or not lengths[0].isascii() or not lengths[0].isdigit()):
            return self._send(400, {'error': 'invalid_length'})
        length = int(lengths[0])
        if not 0 < length <= 1024:
            return self._send(413, {'error': 'request_too_large'})
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                return self._send(400, {'error': 'incomplete_body'})
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            return self._send(400, {'error': 'invalid_json'})
        except OSError:
            return self._send(408, {'error': 'request_timeout'})
        try:
            record = actions.submit(payload, self.server.monitor.status())
        except ActionRejected as error:
            return self._send(400 if error.reason == 'invalid_request' else 409,
                              {'error': error.reason})
        return self._send(202, record)

    def _method_not_allowed(self):
        self._send(405, {'error': 'method_not_allowed'})

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _method_not_allowed

    def log_message(self, fmt, *args):
        LOG.debug('HTTP ' + fmt, *args)


class StatusServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True

    def __init__(self, address, monitor):
        self.monitor = monitor
        self._slots = BoundedSemaphore(8)
        super(StatusServer, self).__init__(address, StatusHandler)

    def get_request(self):
        request, address = super(StatusServer, self).get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, address):
        if not self._slots.acquire(False):
            self.shutdown_request(request)
            return
        try:
            super(StatusServer, self).process_request(request, address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super(StatusServer, self).process_request_thread(request, address)
        finally:
            self._slots.release()


def run_server(monitor, bind, port, stop=None):
    stop = stop or Event()
    # Bind before starting any monitoring/actions, so a busy port fails safely.
    server = StatusServer((bind, port), monitor)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.1})
    thread.daemon = True
    previous = {}
    try:
        if current_thread() is main_thread():
            for number in (signal.SIGINT, signal.SIGTERM):
                previous[number] = signal.signal(number, lambda *_: stop.set())
        thread.start()
        LOG.info('listening on http://%s:%s', *server.server_address)
        monitor.run(stop)
    finally:
        stop.set()
        if thread.is_alive():
            server.shutdown()
            thread.join()
        server.server_close()
        for number, handler in previous.items():
            signal.signal(number, handler)
