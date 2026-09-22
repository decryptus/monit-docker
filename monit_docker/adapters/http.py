"""Bounded local read-only HTTP interface over a cached monitoring service."""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import signal
from socketserver import ThreadingMixIn
from threading import BoundedSemaphore, Event, Thread, current_thread, main_thread
from urllib.parse import urlsplit

from monit_docker.outputs.prometheus import render_metrics

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
        if code == 405:
            self.send_header('Allow', 'GET, HEAD')
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

    def _method_not_allowed(self):
        self._send(405, {'error': 'method_not_allowed'})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _method_not_allowed

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
