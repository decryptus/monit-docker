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
from monit_docker import __version__
from monit_docker.adapters.http import StatusServer
from monit_docker.adapters.http_security import HttpSecurity
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.manual_actions import ManualActions
from monit_docker.audit import AuditJournal
from monit_docker.audit_query import AuditReader
from monit_docker.service import MonitorService
from monit_docker.incident_session import IncidentSessions


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    identifier = 'a' * 64
    token = secrets.token_hex(32)
    audit_token = secrets.token_hex(32)
    ai_token = secrets.token_hex(32)
    model_calls = []
    class Provider:
        def complete(self, prompt, evidence):
            model_calls.append(evidence)
            return dict(analysis=dict(hypotheses=[], missing_evidence=["Synthetic fixture."], suggested_checks=[]))
    calls = []
    actions = ManualActions(lambda *args: calls.append(args))
    monitor = MonitorService(lambda _: CycleResult((ContainerSnapshot(
        id=identifier, name='fixture', status='running'),), ()), interval=.1, manual_actions=actions)
    security = HttpSecurity(action_token=token, action_origin='https://localhost:18443',
                            trust_actor=True, audit_token=audit_token, ai_token=ai_token, ai_origin='https://localhost:18443')
    server = StatusServer(('0.0.0.0', 19808), monitor, security)
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
            monitor.audit_reader = AuditReader(actions.audit)
            (root / 'proxy-audit.conf').write_text('proxy_set_header X-Monit-Audit-Token "' + audit_token + '";\n')
            hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input='local-test-password\n',
                                    text=True, stdout=subprocess.PIPE, check=True).stdout
            (root / 'htpasswd').write_text('test:' + hashed)
            (root / 'htpasswd').chmod(0o644)
            (root / 'ai-htpasswd').write_text('ai-operator:' + hashed)
            (root / 'ai-htpasswd').chmod(0o644)
            (root / 'proxy-ai.conf').write_text('proxy_set_header X-Monit-AI-Token "' + ai_token + '";\n')
            (root / 'ai.conf').write_text(Path('ui/nginx/ai.conf').read_text())
            (root / 'proxy-action.conf').write_text('proxy_set_header X-Monit-Action-Token "' + token + '";\n')
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-keyout', str(root / 'tls.key'), '-out', str(root / 'tls.crt'))
            # Keep the shipped config, changing only the fixture's agent port.
            config = Path('ui/nginx/proxy.conf').read_text().replace(':9808', ':19808')
            (root / 'proxy.conf').write_text(config)
            mounts = ['-v', '%s:/etc/nginx/monit-audit.conf:ro' % (root / 'proxy-audit.conf')]
            for file in ('htpasswd', 'ai-htpasswd', 'proxy-ai.conf', 'proxy-action.conf', 'tls.key', 'tls.crt'):
                mounts.extend(['-v', '%s:/run/secrets/%s:ro' % (root / file, file)])
            mounts.extend(['-v', '%s:/etc/nginx/monit-proxy.conf:ro' % (root / 'proxy.conf')])
            mounts.extend(['-v', '%s:/etc/nginx/monit-ai.conf:ro' % (root / 'ai.conf')])
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
            for path in ('/', '/logs', '/logs.js', '/app.js', '/app.css', '/v1/status', '/v1/actions', '/v1/audit', '/v1/audit/export'):
                body = {} if path == '/v1/actions' else None
                assert request(path, None, body)[0] == 401, path
            code, page, headers = request('/')
            assert code == 200 and b'monit-docker' in page
            expected = Path('VERSION').read_text().strip()
            assert expected == __version__
            assert ('name="monit-ui-version" content="' + expected + '"').encode() in page
            assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
            assert request('/metrics')[0] == 404
            assert request('/healthz')[0] == 404
            assert request('/../etc/passwd')[0] in (400, 404)
            status = json.loads(request('/v1/status')[1])
            assert status['ready'] and token not in json.dumps(status)
            assert status['agent_version'] == expected
            body = dict(request_id=uuid.uuid4().hex, container_id=identifier, action='restart')
            assert request('/v1/actions', body=body)[0] == 403
            assert request('/v1/actions', body=body, extra={'Origin': 'https://evil.example'})[0] == 403
            # A client-provided token must be overwritten by Nginx.
            extra = {'Origin': security.action_origin, 'X-Monit-Action-Token': 'untrusted-client-value', 'X-Monit-Actor': 'forged-user'}
            assert request('/v1/actions', body=body, extra=extra)[0] == 202
            assert request('/v1/actions', body=body, extra=extra)[0] == 202
            for _ in range(30):
                if calls:
                    break
                time.sleep(.1)
            assert calls == [(identifier, 'restart')], calls
            assert all(event['actor'] == 'test' for event in actions.audit.read())
            assert actions.audit.read()[-1]['result'] == 'succeeded'
            assert request('/logs')[0] == 200
            code, data, _ = request('/v1/audit', extra={'X-Monit-Audit-Token': 'forged', 'X-Monit-Actor': 'forged'})
            assert code == 200
            page = json.loads(data)
            assert page['records'] and all(record['actor'] == 'test' for record in page['records'])
            assert audit_token not in json.dumps(status)
            code, data, headers = request('/v1/audit/export?format=csv&cursor=' + page['page_cursor'])
            assert code == 200 and 'attachment;' in headers['Content-Disposition']
            assert b'test' in data
            monitor.audit_reader = None
            assert request('/v1/audit')[0] == 404
            # The private API does not trust an Origin or forwarded username alone.
            req = urllib.request.Request('http://127.0.0.1:19808/v1/actions', json.dumps(body).encode(),
                                         {'Origin': security.action_origin, 'Content-Type': 'application/json',
                                          'X-Forwarded-User': 'test'})
            try:
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as error:
                assert error.code == 403
            else:
                raise AssertionError('direct write bypassed proxy authentication')
            # Default image has no AI route even if the agent capability is enabled.
            monitor.incident_assistant = IncidentSessions(lambda: Provider())
            ai_body = dict(container_id=identifier)
            assert request('/v1/ai/preview', body=ai_body)[0] == 404
            (root / 'ai.conf').write_text(Path('examples/ai/nginx.conf').read_text().replace(':9808', ':19808'))
            command('docker', 'exec', name, 'nginx', '-t')
            command('docker', 'exec', name, 'nginx', '-s', 'reload')
            ai_auth = 'Basic ' + base64.b64encode(b'ai-operator:local-test-password').decode()
            for _ in range(30):
                if request('/v1/ai/preview', body=ai_body)[0] == 401:
                    break
            else:
                raise AssertionError('AI operator access configuration did not reload')
            # Stop the fast fixture polling so consent refers to one stable capture.
            stop.set()
            polling.join(3)
            ai_headers = {'Origin': security.ai_origin, 'X-Monit-AI-Token': 'forged', 'X-Monit-Actor': 'forged'}
            code, data, _ = request('/v1/ai/preview', ai_auth, ai_body, ai_headers)
            assert code == 200, (code, data)
            preview = json.loads(data)
            assert not model_calls
            assert list(monitor.incident_assistant._previews.values())[0][1] == 'ai-operator'
            ai_body['preview_id'] = preview['preview_id']
            code, data, _ = request('/v1/ai/explain', ai_auth, ai_body, ai_headers)
            assert code == 200, (code, data)
            assert model_calls == [preview['context']]
            assert request('/v1/ai/explain', ai_auth, ai_body, ai_headers)[0] == 409
            assert len(model_calls) == 1 and calls == [(identifier, 'restart')]
            print('AI proxy checks passed: disabled default, dedicated operator auth, overwritten secrets/identity, consent and no replay.')
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

