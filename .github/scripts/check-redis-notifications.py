"""Exercise the shipped Compose receiver with real Alertmanager and Redis.

Run only against the disposable CI stack, with the test-only port override.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

STACK = Path(__file__).resolve().parents[2] / 'examples/monitoring'
COMPOSE = ['docker', 'compose', '-f', str(STACK / 'compose.yaml'),
           '-f', str(STACK / 'compose.notifications.yaml'), '-f', str(STACK / 'compose.redis.yaml'),
           '-f', os.environ['REDIS_TEST_OVERRIDE']]
TOKEN = (STACK / 'notifications.local/redis_webhook_token').read_text().strip()
STREAM = 'monit-docker:alerts'


def compose(*args):
    return subprocess.check_output(COMPOSE + list(args), text=True)


def redis(*args):
    return json.loads(compose('exec', '-T', 'redis', 'redis-cli', '--json', *args))


def entries():
    return [(entry_id, json.loads(dict(zip(fields[::2], fields[1::2]))['payload']))
            for entry_id, fields in redis('XRANGE', STREAM, '-', '+')]


def request(path, value=None, token=TOKEN, port=9080):
    data = value if isinstance(value, bytes) else None if value is None else json.dumps(value).encode()
    req = Request('http://127.0.0.1:%s%s' % (port, path), data=data,
                  headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    try:
        with urlopen(req, timeout=10) as result:
            return result.status, result.read()
    except HTTPError as error:
        return error.code, error.read()


def wait_for(check, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (URLError, OSError, subprocess.CalledProcessError):
            pass
        time.sleep(0.5)
    raise AssertionError('Timed out waiting for Redis notification')


def seen(status, name):
    return any(p['status'] == status and any(a['labels']['alertname'] == name for a in p['alerts'])
               for _, p in entries())


def main():
    assert request('/readyz')[0] == 200
    # Prometheus must have discovered the optional Alertmanager target.
    def discovered():
        status, body = request('/api/v1/alertmanagers', port=9090)
        return status == 200 and bool(json.loads(body)['data']['activeAlertmanagers'])
    wait_for(discovered)
    now = datetime.now(timezone.utc)
    alerts = [{'labels': {'alertname': 'RedisNotificationTest', 'job': 'ci', 'instance': 'ci', 'id': name},
               'annotations': {'summary': 'Test ' + name}, 'startsAt': now.isoformat(),
               'endsAt': (now + timedelta(minutes=10)).isoformat()} for name in ('one', 'two')]
    assert request('/api/v2/alerts', alerts, port=9093)[0] == 200
    wait_for(lambda: seen('firing', 'RedisNotificationTest'))
    group = next(p for _, p in entries() if any(a['labels']['alertname'] == 'RedisNotificationTest' for a in p['alerts']))
    assert {a['labels']['id'] for a in group['alerts']} == {'one', 'two'}
    assert group['version'] == '4' and group['groupKey'] and group['truncatedAlerts'] == 0
    assert {a['annotations']['summary'] for a in group['alerts']} == {'Test one', 'Test two'}
    for alert in alerts:
        alert['endsAt'] = datetime.now(timezone.utc).isoformat()
    assert request('/api/v2/alerts', alerts, port=9093)[0] == 200
    wait_for(lambda: seen('resolved', 'RedisNotificationTest'))
    # Invalid requests cannot add entries.
    before = redis('XLEN', STREAM)
    assert request('/alerts', group, token='invalid')[0] == 401
    assert request('/alerts', {})[0] == 400
    assert request('/alerts', b'{')[0] == 415
    assert request('/alerts', b'x' * (1024 * 1024 + 1))[0] == 413
    assert redis('XLEN', STREAM) == before
    saved_id = entries()[0][0]
    compose('stop', 'redis')
    assert request('/healthz')[0] == 200
    assert request('/readyz')[0] == 503
    assert request('/alerts', group)[0] == 503
    retry = dict(alerts[0], labels=dict(alerts[0]['labels'], alertname='RedisRetryTest'),
                 endsAt=(now + timedelta(minutes=10)).isoformat())
    assert request('/api/v2/alerts', [retry], port=9093)[0] == 200
    # Prove Alertmanager attempted delivery during the outage, before restoring Redis.
    wait_for(lambda: 'Redis delivery failed' in compose('logs', '--no-color', 'alertmanager'))
    compose('up', '-d', '--wait', '--wait-timeout', '60', 'redis')
    wait_for(lambda: request('/readyz')[0] == 200)
    assert any(entry_id == saved_id for entry_id, _ in entries()), 'AOF history lost on restart'
    wait_for(lambda: seen('firing', 'RedisRetryTest'), timeout=90)
    # Retention is exact, independent of consumer acknowledgments.
    for _ in range(6):
        assert request('/alerts', group)[0] == 202
    assert redis('XLEN', STREAM) == 5
    print('Redis: grouped firing/resolved, auth, invalid/oversized input, outage retry, AOF restart and retention passed')


if __name__ == '__main__':
    main()
