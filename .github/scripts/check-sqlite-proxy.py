"""Candidate SQLite migration and real TLS proxy acceptance, synthetic monitoring only."""
import base64
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from httpdis.httpdis import crypt
from monit_docker.adapters.auth_migration import migrate
from monit_docker.adapters.local_auth import Accounts
from monit_docker.adapters.auth_http import AuthTransport
from monit_docker.adapters.http import StatusServer
from monit_docker.domain.rules import CycleResult
from monit_docker.service import MonitorService

ORIGIN = 'https://localhost:18444'
PASSWORD = 'synthetic-migration-password'

def command(*args):
    return subprocess.check_output(args, text=True).strip()

def main():
    name = 'monit-sqlite-test-' + uuid.uuid4().hex
    server = thread = accounts = None
    context = ssl._create_unverified_context()  # Disposable test certificate only.
    def request(path, body=None, headers=None):
        time.sleep(.15)
        values = dict(headers or {})
        if body is not None:
            body = json.dumps(body).encode()
            values['Content-Type'] = 'application/json'
        req = urllib.request.Request(ORIGIN + path, body, values)
        try:
            with urllib.request.urlopen(req, context=context, timeout=5) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'users').write_text('reader:' + crypt(PASSWORD, '$6$fixture$') + '\n')
            (root / 'roles').write_text('{"reader":"viewer"}')
            migrate(root / 'users', root / 'roles', root / 'auth.db', root / 'backup', apply=True)
            accounts = Accounts(root / 'auth.db')
            monitor = MonitorService(lambda _: CycleResult((), ()))
            monitor.run_cycle()
            server = StatusServer(('0.0.0.0', 19809), monitor, auth=AuthTransport(accounts, ORIGIN))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            command('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-keyout', str(root / 'tls.key'), '-out', str(root / 'tls.crt'))
            (root / 'proxy.conf').write_text(Path('ui/nginx/sqlite-proxy.conf').read_text().replace(':9808', ':19809'))
            mounts = []
            for source, target in ((root / 'tls.key', '/run/secrets/tls.key'), (root / 'tls.crt', '/run/secrets/tls.crt'),
                    (root / 'proxy.conf', '/etc/nginx/monit-sqlite-proxy.conf'),
                    (Path('ui/nginx/sqlite.conf').resolve(), '/etc/nginx/conf.d/default.conf')):
                mounts += ['-v', '%s:%s:ro' % (source, target)]
            command('docker', 'run', '-d', '--name', name, '--add-host', 'monit-docker:host-gateway',
                    '-p', '127.0.0.1:18444:443', *mounts, 'monit-docker-ui:test')
            for _ in range(30):
                try:
                    if request('/')[0] == 200:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('SQLite proxy did not become ready')
            assert request('/auth.js')[0] == 200
            assert request('/v1/status')[0] == 401
            assert request('/v1/status', headers={'Authorization': 'Basic ' + base64.b64encode(('reader:' + PASSWORD).encode()).decode()})[0] == 401
            assert json.loads(request('/v1/auth/info')[1])['native_auth'] == 'session-v1'
            payload = {'username': 'reader', 'password': PASSWORD}
            code, body, headers = request('/v1/auth/login', payload, {'Origin': ORIGIN})
            assert code == 200, (code, body)
            browser = {'Cookie': headers['Set-Cookie'].split(';')[0], 'Origin': ORIGIN}
            assert 'HttpOnly' in headers['Set-Cookie'] and 'Secure' in headers['Set-Cookie']
            assert request('/v1/status', headers=browser)[0] == 200
            assert request('/v1/auth/logout', {}, browser)[0] == 401
            browser['X-CSRF-Token'] = json.loads(body)['csrf_token']
            assert request('/v1/auth/logout', {}, browser)[0] == 200
            assert request('/v1/status', headers=browser)[0] == 401
            code, body, _ = request('/v1/auth/login', payload, {'X-Monit-Client': 'native-v1'})
            assert code == 200, (code, body)
            grant = json.loads(body)
            native = {'Authorization': 'Session ' + grant['session_token']}
            assert request('/v1/status', headers=native)[0] == 200
            assert request('/v1/auth/users', headers=native)[0] == 403
            accounts.service.disable('reader')
            assert request('/v1/status', headers=native)[0] == 401
            assert request('/metrics')[0] == 404
            assert request('/v1/auth/login/')[0] == 404
            assert request('/v1/auth/login', payload, {'Origin': 'https://foreign.example'})[0] == 401
            print('SQLite migration, native/browser login, TLS proxy, permissions and revocation: OK')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if server is not None:
            server.shutdown()
            if thread is not None:
                thread.join(5)
            server.server_close()
        if accounts is not None:
            accounts.close()

if __name__ == '__main__':
    main()
