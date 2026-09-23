"""Exercise the built Nginx image against the real agent HTTP/service boundary.

The fixture has synthetic containers; no user Docker container is acted upon.
Requires Docker on Linux, openssl and an image tagged monit-docker-ui:test.
"""

import base64
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import tempfile
from threading import Event, Thread
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monit_docker.adapters.http import StatusServer
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.manual_actions import ManualActions
from monit_docker.audit import AuditJournal
from monit_docker.service import MonitorService


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    identifier = 'a' * 64
    token = secrets.token_hex(32)
    calls = []
    actions = ManualActions(lambda *args: calls.append(args), 'https://localhost:18443', token, trust_actor=True)
    monitor = MonitorService(lambda _: CycleResult((ContainerSnapshot(
        id=identifier, name='fixture', status='running'),), ()), interval=.1, manual_actions=actions)
    server = StatusServer(('0.0.0.0', 19808), monitor)
    stop = Event()
    serving = Thread(target=server.serve_forever, kwargs={'poll_interval': .05})
    polling = Thread(target=monitor.run, args=(stop,))
    serving.start()
    polling.start()
    name = 'monit-ui-test-' + uuid.uuid4().hex
    context = ssl._create_unverified_context()  # Only the disposable test certificate.
    auth = 'Basic ' + base64.b64encode(b'test:local-test-password').decode()

    def request(path, authorization=auth, body=None, extra=None):
        time.sleep(.11)  # Stay below the production proxy's per-IP rate limit.
        headers = {'Authorization': authorization} if authorization else {}
        headers.update(extra or {})
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        req = urllib.request.Request('https://127.0.0.1:18443' + path, body, headers)
        try:
            with urllib.request.urlopen(req, context=context, timeout=3) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            actions.audit = AuditJournal(root / 'events.jsonl', emit=False)
            hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input='local-test-password\n',
                                    text=True, stdout=subprocess.PIPE, check=True).stdout
            (root / 'htpasswd').write_text('test:' + hashed)
            (root / 'htpasswd').chmod(0o644)
            (root / 'proxy-action.conf').write_text('proxy_set_header X-Monit-Action-Token "' + token + '";\n')
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-keyout', str(root / 'tls.key'), '-out', str(root / 'tls.crt'))
            # Keep the shipped config, changing only the fixture's agent port.
            config = Path('ui/nginx/proxy.conf').read_text().replace(':9808', ':19808')
            (root / 'proxy.conf').write_text(config)
            mounts = []
            for file in ('htpasswd', 'proxy-action.conf', 'tls.key', 'tls.crt'):
                mounts.extend(['-v', '%s:/run/secrets/%s:ro' % (root / file, file)])
            mounts.extend(['-v', '%s:/etc/nginx/monit-proxy.conf:ro' % (root / 'proxy.conf')])
            command('docker', 'run', '-d', '--name', name, '--add-host', 'monit-docker:host-gateway',
                    '-p', '127.0.0.1:18443:443', *mounts, 'monit-docker-ui:test')
            for _ in range(50):
                try:
                    if request('/', None)[0] == 401:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('Nginx did not become ready')
            for path in ('/', '/app.js', '/app.css', '/v1/status', '/v1/actions'):
                body = {} if path == '/v1/actions' else None
                assert request(path, None, body)[0] == 401, path
            code, page, headers = request('/')
            assert code == 200 and b'monit-docker' in page
            assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
            assert request('/metrics')[0] == 404
            assert request('/healthz')[0] == 404
            assert request('/../etc/passwd')[0] in (400, 404)
            status = json.loads(request('/v1/status')[1])
            assert status['ready'] and token not in json.dumps(status)
            body = dict(request_id=uuid.uuid4().hex, container_id=identifier, action='restart')
            assert request('/v1/actions', body=body)[0] == 403
            assert request('/v1/actions', body=body, extra={'Origin': 'https://evil.example'})[0] == 403
            # A client-provided token must be overwritten by Nginx.
            extra = {'Origin': actions.origin, 'X-Monit-Action-Token': 'untrusted-client-value', 'X-Monit-Actor': 'forged-user'}
            assert request('/v1/actions', body=body, extra=extra)[0] == 202
            assert request('/v1/actions', body=body, extra=extra)[0] == 202
            for _ in range(30):
                if calls:
                    break
                time.sleep(.1)
            assert calls == [(identifier, 'restart')], calls
            assert all(event['actor'] == 'test' for event in actions.audit.read())
            assert actions.audit.read()[-1]['result'] == 'succeeded'
            # The private API does not trust an Origin or forwarded username alone.
            req = urllib.request.Request('http://127.0.0.1:19808/v1/actions', json.dumps(body).encode(),
                                         {'Origin': actions.origin, 'Content-Type': 'application/json',
                                          'X-Forwarded-User': 'test'})
            try:
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('direct write bypassed proxy authentication')
            print('Nginx checks passed: TLS, auth, private metrics, proxy secret, CSRF and deduplication.')
    except Exception:
        subprocess.run(['docker', 'logs', name], check=False)
        raise
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, check=False)
        stop.set()
        server.shutdown()
        serving.join(3)
        polling.join(3)
        server.server_close()


if __name__ == '__main__':
    main()
