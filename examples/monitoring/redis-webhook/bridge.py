"""Optional Alertmanager webhook: HTTPdis -> dwho strict send -> Redis Stream."""

import hmac
import json
import logging
import math
import os
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from dwho.adapters.redis import DWhoAdapterRedis
from dwho.classes.notifiers import DWhoNotifierRedis
from httpdis import httpdis

LOG = logging.getLogger('monit-docker.redis-webhook')


def redis_config(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('redis', 'rediss') or not parsed.hostname:
        raise ValueError('Redis URL must use redis:// or rediss:// with a host')
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for option in ('socket_timeout', 'socket_connect_timeout'):
        timeout = float(query.setdefault(option, '3'))
        if not math.isfinite(timeout) or not 0 < timeout <= 5:
            raise ValueError('Redis socket timeouts must be between 0 and 5 seconds')
    # This example pins redis-py 5.2.1: RESP2 and no timeout retries.
    query['protocol'] = '2'
    query['retry_on_timeout'] = 'False'
    return {'general': {'uri': urlunsplit(parsed._replace(query=urlencode(query))),
                        'redis_mode': 'stream'}}


def validate_payload(payload):
    if not isinstance(payload, dict) or payload.get('version') != '4':
        raise ValueError('Expected an Alertmanager webhook version 4 object')
    if payload.get('status') not in ('firing', 'resolved'):
        raise ValueError('Expected firing or resolved status')
    if not isinstance(payload.get('groupKey'), str):
        raise ValueError('Missing groupKey')
    if payload.get('truncatedAlerts', 0) != 0:
        raise ValueError('Truncated notifications are not accepted; set max_alerts to 0')
    alerts = payload.get('alerts')
    if not isinstance(alerts, list) or not alerts:
        raise ValueError('Expected a nonempty alerts list')
    for alert in alerts:
        if not isinstance(alert, dict) or alert.get('status') not in ('firing', 'resolved'):
            raise ValueError('Invalid alert')
        labels = alert.get('labels')
        if not isinstance(labels, dict) or not isinstance(labels.get('alertname'), str) or not labels['alertname']:
            raise ValueError('Missing alertname label')
        if not isinstance(alert.get('annotations'), dict):
            raise ValueError('Expected alert annotations')
    return payload


class RedisWebhook:
    def __init__(self, url, token, stream='monit-docker:alerts', maxlen=10000):
        if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
            raise ValueError('Webhook token must contain at least 32 non-whitespace characters')
        if not stream or not isinstance(maxlen, int) or isinstance(maxlen, bool) or not 0 < maxlen <= 9223372036854775807:
            raise ValueError('Expected a stream name and positive 64-bit maxlen')
        self.token = token
        self.stream = stream
        self.cfg = redis_config(url)
        self.cfg['general']['stream_maxlen'] = maxlen
        self.notifier = DWhoNotifierRedis()

    def authorized(self, header):
        expected = ('Bearer ' + self.token).encode('utf-8')
        return hmac.compare_digest((header or '').encode('utf-8'), expected)

    def health(self, request):
        return httpdis.HttpResponseJson(data={'status': 'alive'})

    def ready(self, request):
        adapter = None
        try:
            adapter = DWhoAdapterRedis({'general': {'redis': {'probe': {'url': self.cfg['general']['uri']}}}})
            if adapter.ping() != {'probe': True}:
                raise RuntimeError('Redis ping failed')
            return httpdis.HttpResponseJson(data={'status': 'ready'})
        except Exception:
            return httpdis.HttpResponseJson(code=503, data={'error': 'Redis unavailable'})
        finally:
            if adapter:
                adapter.disconnect()

    def receive(self, request):
        if request.headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return httpdis.HttpResponseJson(code=415, data={'error': 'Expected application/json'})
        try:
            payload = validate_payload(request.payload_params())
        except ValueError as error:
            return httpdis.HttpResponseJson(code=400, data={'error': str(error)})
        try:
            result = self.notifier.send('alertmanager', self.cfg, {'key': self.stream, 'value': payload})
            entry_id = result['notifier']
            if isinstance(entry_id, bytes):
                entry_id = entry_id.decode('ascii')
            return httpdis.HttpResponseJson(code=202, data={'stream': self.stream, 'id': entry_id})
        except Exception as error:
            # Avoid logging credentials, request bodies or Redis error strings.
            LOG.warning('Redis delivery failed (%s)', type(error).__name__)
            return httpdis.HttpResponseJson(code=503, data={'error': 'Redis delivery failed'})


def run(bridge, host='0.0.0.0', port=9080):
    class Handler(httpdis.HttpReqHandler):
        timeout = 5
        _ALLOWED_CONTENT_TYPES = ['application/json']
        _ALLOWED_MULTIPART_FORM = False

        def authenticate(self, users=None):
            if not bridge.authorized(self.headers.get('Authorization')):
                raise self.req_error(401, 'Invalid webhook credentials')

        @staticmethod
        def parse_payload(data, charset):
            def reject_constant(value):
                raise ValueError('Non-finite JSON value')
            return json.loads(data.decode(charset), parse_constant=reject_constant)

    httpdis.register(bridge.receive, 'POST', name='alerts', to_auth=True, to_log=False)
    httpdis.register(bridge.health, ['GET', 'HEAD'], name='healthz', to_log=False)
    httpdis.register(bridge.ready, ['GET', 'HEAD'], name='readyz', to_log=False)
    options = {'listen_addr': host, 'listen_port': port, 'max_body_size': 1024 * 1024, 'max_workers': 4}
    httpdis.init(options)
    httpdis.run(options, http_req_handler=Handler)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    # Credentials are read once; restart the service to rotate them.
    webhook = RedisWebhook(
        Path(os.environ.get('REDIS_URL_FILE', '/run/secrets/redis_url')).read_text().strip(),
        Path(os.environ.get('WEBHOOK_TOKEN_FILE', '/run/secrets/redis_webhook_token')).read_text().strip(),
        stream=os.environ.get('REDIS_STREAM', 'monit-docker:alerts'),
        maxlen=int(os.environ.get('REDIS_STREAM_MAXLEN', '10000')))
    run(webhook, host=os.environ.get('WEBHOOK_BIND', '0.0.0.0'), port=int(os.environ.get('WEBHOOK_PORT', '9080')))
