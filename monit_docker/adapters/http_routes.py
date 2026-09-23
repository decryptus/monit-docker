"""HTTPdis route declarations and serialization for the monitoring API."""

import json
import io
from urllib.parse import urlsplit

from httpdis import httpdis

from monit_docker.domain.errors import ActionRejected
from monit_docker.audit import AuditError, export_events
from monit_docker.audit_query import QueryError
from monit_docker.outputs.prometheus import render_metrics


_AUDIT_EXPORT_TYPES        = {'jsonl': 'application/x-ndjson; charset=utf-8', 'csv': 'text/csv; charset=utf-8'}
_AUDIT_READ_PATHS          = ('/v1/audit', '/v1/audit/export')

_ACTION_MAX_BODY_SIZE       = 1024
_NOTIFICATION_MAX_BODY_SIZE = 64 * 1024

_READ_METHODS       = ('GET', 'HEAD')
_WRITE_METHODS      = ('POST',)
_JSON_CONTENT_TYPE  = 'application/json; charset=utf-8'
_METRICS_TYPE       = 'text/plain; version=0.0.4; charset=utf-8'
_HEALTH_RESPONSE    = {'alive': True}
_REJECTION_CODES    = {'invalid_request': 400, 'container_protected': 403}
_RESPONSE_HEADERS   = {'Cache-Control':          'no-store',
                       'X-Content-Type-Options': 'nosniff',
                       'Referrer-Policy':        'no-referrer'}


def json_response(data, code = 200):
    headers                 = _RESPONSE_HEADERS.copy()
    headers['Content-Type'] = _JSON_CONTENT_TYPE
    body                    = json.dumps(data, allow_nan = False) + '\n'
    return httpdis.HttpResponse(code, body, headers)


def health(request):
    return json_response(_HEALTH_RESPONSE)


def readiness(request):
    ready = request.server.monitor.status()['ready']
    return json_response(dict(ready = ready), 200 if ready else 503)


def status(request):
    return json_response(request.server.monitor.status())


def metrics(request):
    headers                 = _RESPONSE_HEADERS.copy()
    headers['Content-Type'] = _METRICS_TYPE
    body                    = render_metrics(request.server.monitor.status())
    return httpdis.HttpResponse(200, body, headers)


def submit_action(request):
    monitor = request.server.monitor
    try:
        record = monitor.manual_actions.submit(request.payload_params(), monitor.status(), actor=getattr(request, 'audit_actor', 'anonymous'))
    except AuditError:
        return json_response(dict(error='audit_unavailable'), 503)
    except ActionRejected as error:
        code = _REJECTION_CODES.get(error.reason, 409)
        return json_response(dict(error = error.reason), code)
    return json_response(record, 202)


def notification(request):
    try:
        count = request.server.monitor.notification_audit.receive(request.payload_params())
    except ValueError:
        return json_response(dict(error='invalid_notification'), 400)
    except AuditError:
        return json_response(dict(error='audit_unavailable'), 503)
    return json_response(dict(recorded=count, delivery_status='not_reported'), 202)


def audit_page(request):
    try:
        page, format = request.server.monitor.audit_reader.page(urlsplit(request.path).query, request.audit_actor)
        if request._path == '/v1/audit/export':
            output = io.StringIO()
            export_events(page['records'], output, format)
            headers = _RESPONSE_HEADERS.copy()
            headers['Content-Type'] = _AUDIT_EXPORT_TYPES[format]
            headers['Content-Disposition'] = 'attachment; filename="monit-docker-events.' + format + '"'
            return httpdis.HttpResponse(200, output.getvalue(), headers)
        return json_response(page)
    except QueryError as error:
        return json_response(dict(error=error.reason), error.code)


_ROUTES = ({'name': 'v1/audit', 'op': _READ_METHODS, 'handler': audit_page, 'to_auth': True},
           {'name': 'v1/audit/export', 'op': _READ_METHODS, 'handler': audit_page, 'to_auth': True},
           {'name': 'v1/notifications', 'op': _WRITE_METHODS, 'handler': notification,
            'to_auth': True, 'max_body_size': _NOTIFICATION_MAX_BODY_SIZE},
           {'name': 'healthz',     'op': _READ_METHODS,  'handler': health},
           {'name': 'readyz',     'op': _READ_METHODS,  'handler': readiness},
           {'name': 'v1/status',  'op': _READ_METHODS,  'handler': status},
           {'name': 'metrics',    'op': _READ_METHODS,  'handler': metrics},
           {'name': 'v1/actions', 'op': _WRITE_METHODS, 'handler': submit_action,
            'to_auth': True, 'max_body_size': _ACTION_MAX_BODY_SIZE})

_ALLOWED_METHODS = {'/' + route['name']: ', '.join(route['op']) for route in _ROUTES}


def allowed_methods(path):
    return _ALLOWED_METHODS.get(path, ', '.join(_READ_METHODS))


def register_routes():
    for route in _ROUTES:
        httpdis.register(to_log = False, **route)
