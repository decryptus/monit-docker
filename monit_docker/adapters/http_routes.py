"""HTTPdis route declarations and serialization for the monitoring API."""

import json

from httpdis import httpdis

from monit_docker.domain.errors import ActionRejected
from monit_docker.outputs.prometheus import render_metrics


_READ_METHODS       = ('GET', 'HEAD')
_WRITE_METHODS      = ('POST',)
_JSON_CONTENT_TYPE  = 'application/json; charset=utf-8'
_METRICS_TYPE       = 'text/plain; version=0.0.4; charset=utf-8'
_HEALTH_RESPONSE    = {'alive': True}
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
        record = monitor.manual_actions.submit(request.payload_params(), monitor.status())
    except ActionRejected as error:
        code = 400 if error.reason == 'invalid_request' else 409
        return json_response(dict(error = error.reason), code)
    return json_response(record, 202)


_ROUTES = ({'name': 'healthz',     'op': _READ_METHODS,  'handler': health},
           {'name': 'readyz',     'op': _READ_METHODS,  'handler': readiness},
           {'name': 'v1/status',  'op': _READ_METHODS,  'handler': status},
           {'name': 'metrics',    'op': _READ_METHODS,  'handler': metrics},
           {'name': 'v1/actions', 'op': _WRITE_METHODS, 'handler': submit_action,
            'to_auth': True})

_ALLOWED_METHODS = {'/' + route['name']: ', '.join(route['op']) for route in _ROUTES}


def allowed_methods(path):
    return _ALLOWED_METHODS.get(path, ', '.join(_READ_METHODS))


def register_routes():
    for route in _ROUTES:
        httpdis.register(to_log = False, **route)
