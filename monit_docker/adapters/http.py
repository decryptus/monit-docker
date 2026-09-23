"""HTTPdis transport and Sonicprobe workers for the cached monitoring service."""

import hmac
import json
import logging
import signal
from threading import Event, Lock, Thread, current_thread, main_thread
from urllib.parse import urlsplit

from httpdis import httpdis
from sonicprobe.libs.threading_tcp_server import KillableThreadingHTTPServer

from monit_docker.adapters.http_routes import (allowed_methods,
                                               json_response,
                                               register_routes)


LOG                    = logging.getLogger('monit-docker')
_MAX_LENGTH_DIGITS     = 5
_REQUEST_TIMEOUT       = 5
_READ_METHODS          = ('GET', 'HEAD')
_JSON_CONTENT_TYPES    = ('application/json',)
_SHUTDOWN_SIGNALS      = (signal.SIGINT, signal.SIGTERM)
_SERVER_OPTIONS        = {'max_workers':    8,
                          'max_body_size': 65536,
                          'max_requests':  0,
                          'max_life_time': 0}
_ERROR_MESSAGES        = {400: 'invalid_request',
                          403: 'forbidden',
                          404: 'not_found',
                          405: 'method_not_allowed',
                          408: 'request_timeout',
                          413: 'request_too_large',
                          415: 'json_required',
                          500: 'internal_error'}
_API_ERRORS            = frozenset(_ERROR_MESSAGES.values()) | frozenset((
                          'origin_rejected', 'invalid_length', 'invalid_json'))
_INITIALIZATION_LOCK   = Lock()
_INITIALIZED           = False


def _initialize_httpdis():
    # HTTPdis routes/options are process-global. Register stateless callbacks
    # once; each callback reads its service from the accepting server instance.
    global _INITIALIZED
    with _INITIALIZATION_LOCK:
        if not _INITIALIZED:
            register_routes()
            httpdis.init(_SERVER_OPTIONS.copy(), use_sigterm_handler = False)
            _INITIALIZED = True


class StatusHandler(httpdis.HttpReqHandler):
    server_version         = 'monit-docker'
    sys_version            = ''
    timeout                = _REQUEST_TIMEOUT
    _ALLOWED_CONTENT_TYPES = _JSON_CONTENT_TYPES
    _ALLOWED_MULTIPART_FORM = False
    _FUNC_SEND_ERROR       = 'send_api_error'

    def send_api_error(self, code, message, headers = None):
        if code == 404 and self.command not in _READ_METHODS:
            code = 405
        error = message if message in _API_ERRORS else _ERROR_MESSAGES.get(code, 'request_failed')
        response = json_response(dict(error = error), code)
        if code == 405:
            response.add_header('Allow', allowed_methods(urlsplit(self.path).path))
        self.end_response(response)

    def authenticate(self, auth_users = None):
        if urlsplit(self.path).path == '/v1/notifications':
            receiver = self.server.monitor.notification_audit
            if receiver is None:
                raise self.req_error(405)
            tokens = self.headers.get_all('Authorization')
            if not tokens or len(tokens) != 1 or not hmac.compare_digest(tokens[0].encode('utf-8'), ('Bearer ' + receiver.token).encode('ascii')):
                raise self.req_error(403, 'forbidden')
            return
        actions = self.server.monitor.manual_actions
        if actions is None:
            raise self.req_error(405)
        tokens  = self.headers.get_all('X-Monit-Action-Token')
        origins = self.headers.get_all('Origin')
        if (not tokens or len(tokens) != 1
                or not hmac.compare_digest(tokens[0].encode('utf-8'), actions.token.encode('ascii'))):
            raise self.req_error(403, 'forbidden')
        if not origins or len(origins) != 1 or origins[0] != actions.origin:
            raise self.req_error(403, 'origin_rejected')
        self.audit_actor = 'anonymous'
        if actions.trust_actor:
            actors = self.headers.get_all('X-Monit-Actor')
            if (not actors or len(actors) != 1 or not actors[0].strip() or len(actors[0]) > 128
                    or any(ord(c) < 32 or ord(c) == 127 for c in actors[0])):
                raise self.req_error(403, 'forbidden')
            self.audit_actor = actors[0]

    def data_from_payload(self, cmd):
        # Tighten HTTPdis's general-purpose parser for this small JSON API.
        # Routing, per-route body limits, authentication and parsing remain in HTTPdis.
        if self.command != 'POST':
            raise self.req_error(405)
        notification = urlsplit(self.path).path == '/v1/notifications'
        content_type = self.headers.get('Content-Type', '').lower()
        if (content_type.split(';', 1)[0].strip() if notification else content_type) not in _JSON_CONTENT_TYPES:
            raise self.req_error(415)
        lengths = self.headers.get_all('Content-Length')
        if (self.headers.get('Transfer-Encoding') is not None
                or not lengths or len(lengths) != 1
                or len(lengths[0]) > _MAX_LENGTH_DIGITS
                or not lengths[0].isascii() or not lengths[0].isdigit()):
            raise self.req_error(400, 'invalid_length')
        if int(lengths[0]) == 0:
            raise self.req_error(413)
        try:
            return super(StatusHandler, self).data_from_payload(cmd)
        except OSError:
            raise self.req_error(408)

    @staticmethod
    def parse_payload(data, charset):
        try:
            return json.loads(data.decode(charset))
        except (ValueError, UnicodeError):
            raise httpdis.HttpReqError(400, 'invalid_json')

    def do_POST(self):
        if (self.server.monitor.manual_actions is None
                and not (urlsplit(self.path).path == '/v1/notifications' and self.server.monitor.notification_audit)):
            self.send_api_error(405, 'method_not_allowed')
            return
        super(StatusHandler, self).do_POST()

    def do_OPTIONS(self):
        # The API deliberately has no CORS endpoint.
        self.send_api_error(405, 'method_not_allowed')

    def end_response(self, response):
        if self.command == 'HEAD':
            # Preserve the representation length advertised by this API.
            body = response.data
            self._head_length = len(body.encode('utf-8') if isinstance(body, str) else body or b'')
        super(StatusHandler, self).end_response(response)

    def send_header(self, keyword, value):
        if self.command == 'HEAD' and keyword.lower() == 'content-length':
            value = str(getattr(self, '_head_length', 0))
        super(StatusHandler, self).send_header(keyword, value)

    def log_request(self, code = '-', size = '-'):
        LOG.debug('HTTP %s %s %s', self.command, code, size)


class StatusServer(KillableThreadingHTTPServer):
    def __init__(self, address, monitor):
        _initialize_httpdis()
        self.monitor = monitor
        super(StatusServer, self).__init__(_SERVER_OPTIONS.copy(), address,
                                           StatusHandler, name = 'monit-docker-http')

    def serve_forever(self, poll_interval = 0.5):
        # Use Sonicprobe's bounded worker queue, not ThreadingMixIn dispatch.
        self.serve_until_killed()

    def shutdown(self):
        self.kill()

    def server_close(self):
        if hasattr(self, '_request_lock'):
            self.kill()
        super(StatusServer, self).server_close()


def run_server(monitor, bind, port, stop = None):
    stop     = stop or Event()
    server   = StatusServer((bind, port), monitor)
    thread   = Thread(target = server.serve_forever, name = 'monit-docker-http')
    previous = {}
    thread.daemon = True
    try:
        if current_thread() is main_thread():
            for number in _SHUTDOWN_SIGNALS:
                previous[number] = signal.signal(number, lambda *_: stop.set())
        thread.start()
        LOG.info('listening on http://%s:%s', *server.server_address)
        monitor.run(stop)
    finally:
        stop.set()
        server.shutdown()
        if thread.is_alive():
            thread.join()
        server.server_close()
        for number, handler in previous.items():
            signal.signal(number, handler)
