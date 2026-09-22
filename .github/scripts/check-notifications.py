"""Exercise the shipped receivers with real Alertmanager and local test servers.

No external SMTP server or Slack workspace is contacted. Docker mode is Linux CI;
--alertmanager-bin supports a locally downloaded binary for development.
"""

import argparse
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import socket
import ssl
import subprocess
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
import yaml

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / 'examples/monitoring'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    with urlopen(Request(url, data=data, headers={'Content-Type': 'application/json'}), timeout=5) as response:
        return response.read()


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except (URLError, OSError):
            pass
        time.sleep(0.2)
    raise AssertionError('Timed out waiting for local Alertmanager test')


def exercise(path, binary):
    mail, slack = queue.Queue(), queue.Queue()

    class SMTPHandler:
        async def handle_DATA(self, server, session, envelope):
            assert session.ssl and session.authenticated
            assert envelope.rcpt_tos == ['operator@example.invalid']
            mail.put(BytesParser(policy=policy.default).parsebytes(envelope.content))
            return '250 accepted by local test sink'

    class SlackHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            slack.put(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *args):
            pass

    def authenticate(server, session, envelope, mechanism, credentials):
        valid = (isinstance(credentials, LoginPassword)
                 and credentials.login == b'monitoring@example.invalid'
                 and credentials.password == b'local-test-password')
        return AuthResult(success=valid, handled=False)

    with tempfile.TemporaryDirectory(prefix='monit-notifications-') as directory:
        temporary = Path(directory)
        # Disposable fixture files must be readable by the image's nobody user.
        temporary.chmod(0o755)
        cert, key = temporary / 'cert.pem', temporary / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        smtp = Controller(SMTPHandler(), hostname='127.0.0.1', port=free_port(),
                          tls_context=context, require_starttls=True, auth_required=True,
                          auth_exclude_mechanism=['LOGIN'],
                          authenticator=authenticate)
        smtp.start()
        http = ThreadingHTTPServer(('127.0.0.1', 0), SlackHandler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        process = None
        container = 'monit-notifications-' + temporary.name
        log = temporary / 'alertmanager.log'
        try:
            config = yaml.safe_load(path.read_text())
            config['route'].update(group_wait='1s', group_interval='2s', repeat_interval='1h')
            receiver = config['receivers'][0]
            email_enabled = bool(receiver.get('email_configs'))
            slack_enabled = bool(receiver.get('slack_configs'))
            (temporary / 'password').write_text('local-test-password')
            (temporary / 'webhook').write_text('http://127.0.0.1:%s/' % http.server_port)
            for item in receiver.get('email_configs', []):
                item['smarthost'] = '127.0.0.1:%s' % smtp.port
                item['auth_password_file'] = str(temporary / 'password')
                item['tls_config'] = {'ca_file': str(cert), 'server_name': 'localhost'}
                assert item['require_tls'] and item['send_resolved']
            for item in receiver.get('slack_configs', []):
                item['api_url_file'] = str(temporary / 'webhook')
                assert item['send_resolved']
            local_config = temporary / 'alertmanager.yml'
            local_config.write_text(yaml.safe_dump(config))
            for item in (cert, temporary / 'password', temporary / 'webhook', local_config):
                item.chmod(0o644)
            port = free_port()
            flags = ['--config.file=' + str(local_config), '--cluster.listen-address=',
                     '--web.listen-address=127.0.0.1:%s' % port, '--storage.path=/tmp/alertmanager-test']
            if binary:
                flags[-1] = '--storage.path=' + str(temporary / 'data')
                command = [binary] + flags
            else:
                image = yaml.safe_load((STACK / 'compose.notifications.yaml').read_text())['services']['alertmanager']['image']
                command = ['docker', 'run', '--rm', '--name', container, '--network', 'host',
                           '-v', '%s:%s:ro' % (temporary, temporary), image] + flags
            with log.open('w') as stream:
                process = subprocess.Popen(command, stdout=stream, stderr=stream)
            base = 'http://127.0.0.1:%s' % port
            wait_for(lambda: request(base + '/-/ready'))
            now = datetime.now(timezone.utc)
            alerts = [{'labels': {'alertname': 'MonitDockerContainerMemoryHigh', 'job': 'monit-docker',
                                  'instance': 'test-agent:9808', 'id': name, 'name': name, 'severity': 'warning'},
                       'annotations': {'summary': 'High memory on ' + name, 'description': 'Local notification test',
                                       'runbook_url': 'https://example.invalid/runbook'},
                       'startsAt': now.isoformat(), 'endsAt': (now + timedelta(minutes=5)).isoformat()}
                      for name in ('web-one', 'web-two')]

            def verify_message(status):
                if email_enabled:
                    message = mail.get(timeout=30)
                    assert status in str(message['Subject'])
                    body = message.get_body(preferencelist=('plain',)).get_content()
                    assert 'web-one' in body and 'web-two' in body, body
                if slack_enabled:
                    message = slack.get(timeout=30)
                    attachment = message['attachments'][0]
                    assert status in attachment['title'], attachment
                    assert 'web-one' in attachment['text'] and 'web-two' in attachment['text']

            request(base + '/api/v2/alerts', alerts)
            verify_message('FIRING')
            request(base + '/api/v2/alerts', alerts)
            time.sleep(2.5)
            assert mail.empty() and slack.empty(), 'Duplicate or unconfigured notification'
            for alert in alerts:
                alert['endsAt'] = datetime.now(timezone.utc).isoformat()
            request(base + '/api/v2/alerts', alerts)
            verify_message('RESOLVED')
            assert mail.empty() and slack.empty()
            print('Verified %s: grouped firing, deduplication and recovery%s' %
                  (path.name, ' over authenticated SMTP STARTTLS' if email_enabled else ''), flush=True)
        except Exception:
            if log.exists():
                print(log.read_text()[-6000:])  # Only disposable local fixture values.
            raise
        finally:
            if process:
                if not binary:
                    subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL, check=False)
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            http.shutdown()
            http.server_close()
            smtp.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--alertmanager-bin')
    args = parser.parse_args()
    basic = yaml.safe_load((STACK / 'prometheus.yml').read_text())
    notifications = yaml.safe_load((STACK / 'prometheus-notifications.yml').read_text())
    assert notifications.pop('alerting') == {
        'alertmanagers': [{'static_configs': [{'targets': ['alertmanager:9093']}]}]}
    assert basic == notifications, 'Notification config changed collection or rules'
    email = yaml.safe_load((STACK / 'alertmanager.email.yml').read_text())['receivers'][0]['email_configs']
    slack = yaml.safe_load((STACK / 'alertmanager.slack.yml').read_text())['receivers'][0]['slack_configs']
    both = yaml.safe_load((STACK / 'alertmanager.email-slack.yml').read_text())['receivers'][0]
    assert both['email_configs'] == email and both['slack_configs'] == slack
    for name in ('email', 'slack', 'email-slack'):
        exercise(STACK / ('alertmanager.%s.yml' % name), args.alertmanager_bin)


if __name__ == '__main__':
    main()
