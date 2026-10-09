"""Exercise the root Compose, optional UI and memory lab on a disposable runner."""

import base64
import json
import os
from pathlib import Path
import secrets
import ssl
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
BASE_FILES = ['docker-compose.yml']
LAB_FILES = BASE_FILES + ['docker-compose.ui.yml', 'examples/tutorial-memory/compose.yaml']
WORKER = 'monit-docker-tutorial-memory'
AUDIT_FILE = '/var/lib/monit-docker/audit/events.jsonl'
ENV = dict(os.environ, COMPOSE_PROJECT_NAME='monit-quickstart-ci',
           DEMO_SCENARIO='demo-observe', MONIT_DOCKER_PORT='19808', UI_PORT='18443')


def compose(files, *args):
    command = ['docker', 'compose']
    for filename in files:
        command.extend(['-f', filename])
    return subprocess.check_output(command + list(args), cwd=ROOT, env=ENV, text=True)


def wait_for(check, description, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                print('Verified: ' + description, flush=True)
                return result
        except (URLError, OSError, KeyError):
            pass
        time.sleep(2)
    raise AssertionError('Timed out: ' + description)


def main():
    secret_dir = ROOT / 'examples/ui/secrets.local'
    if secret_dir.exists():
        raise RuntimeError('Use a clean disposable checkout; secrets.local already exists')
    password = secrets.token_hex(24)
    subprocess.run(['python3', 'examples/ui/prepare.py', '--self-signed'],
                   input='compose-ci\n' + password + '\n' + password + '\n',
                   text=True, cwd=ROOT, check=True)
    tls = ssl.create_default_context(cafile=str(secret_dir / 'tls.crt'))
    auth = 'Basic ' + base64.b64encode(('compose-ci:' + password).encode()).decode()

    def request(path='/v1/status', browser=False, authenticated=True):
        base = 'https://localhost:18443' if browser else 'http://127.0.0.1:19808'
        headers = {'Authorization': auth} if browser and authenticated else {}
        with urlopen(Request(base + path, headers=headers),
                     context=tls if browser else None, timeout=5) as response:
            return json.load(response)

    def started():
        return subprocess.check_output(['docker', 'inspect', '--format',
                                        '{{.State.StartedAt}}', WORKER], text=True).strip()

    try:
        compose(BASE_FILES, 'up', '-d', '--wait', '--wait-timeout', '180')
        wait_for(lambda: request()['ready'], 'minimal agent collects actual containers')
        assert not request()['manual_actions']['enabled']
        compose(BASE_FILES, 'down')

        compose(LAB_FILES, 'up', '-d', '--wait', '--wait-timeout', '180')
        try:
            request(browser=True, authenticated=False)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError('UI allowed anonymous access')
        status = wait_for(lambda: request(browser=True), 'authenticated UI reaches the agent')
        assert [c['name'] for c in status['containers']] == [WORKER]
        assert not status['manual_actions']['enabled']
        original_start = started()
        compose(LAB_FILES, 'kill', '--signal', 'SIGUSR1', 'demo-worker')
        wait_for(lambda: request()['containers'][0]['mem_percent'] > 60,
                 'bounded worker exceeds the configured threshold')
        preview = compose(LAB_FILES, 'exec', '-T', 'monit-docker', 'monit-docker',
                          '--name', WORKER, 'monit', '--dry-run', '--cmd-if',
                          'mem_percent > 60 ? restart')
        assert 'dry-run' in preview
        assert started() == original_start, 'Observation or preview restarted the worker'

        ENV['DEMO_SCENARIO'] = 'demo-guard'
        compose(LAB_FILES, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'monit-docker')
        compose(LAB_FILES, 'restart', 'ui')
        wait_for(lambda: started() != original_start, 'guard restarts the selected worker')
        wait_for(lambda: request(browser=True)['containers'][0]['mem_percent'] < 60,
                 'UI observes recovery after agent recreation')
        journal = compose(LAB_FILES, 'exec', '-T', 'monit-docker', 'monit-docker',
                          '--audit-file', AUDIT_FILE, 'audit-export')
        events = [json.loads(line) for line in journal.splitlines() if line.strip()]
        assert any(e.get('source') == 'automatic' and e.get('result') == 'succeeded'
                   for e in events), 'Missing successful automatic action event'
        # Consume another incident and wait out the cooldown: the persisted budget
        # must prevent a second automatic restart for this same container ID.
        recovered_start = started()
        compose(LAB_FILES, 'kill', '--signal', 'SIGUSR1', 'demo-worker')
        wait_for(lambda: request()['actions']['restart-limit'] > 0,
                 'second incident is blocked by the restart budget', timeout=180)
        assert started() == recovered_start

        ENV['DEMO_SCENARIO'] = 'demo-observe'
        compose(LAB_FILES, 'up', '-d', '--no-deps', '--wait', '--wait-timeout', '180', 'monit-docker')
        compose(LAB_FILES, 'restart', 'ui')
        compose(LAB_FILES, 'kill', '--signal', 'SIGUSR2', 'demo-worker')
        wait_for(lambda: request()['containers'][0]['mem_percent'] < 60,
                 'worker releases memory on command')
        before = compose(LAB_FILES, 'exec', '-T', 'monit-docker', 'monit-docker',
                         '--audit-file', AUDIT_FILE, 'audit-export')
        compose(LAB_FILES, 'down')
        compose(LAB_FILES, 'up', '-d', '--wait', '--wait-timeout', '180')
        after = compose(LAB_FILES, 'exec', '-T', 'monit-docker', 'monit-docker',
                        '--audit-file', AUDIT_FILE, 'audit-export')
        assert before == after, 'Observation restart changed or lost the retained journal'
        print('Verified: retained journal survives down/up', flush=True)
    finally:
        compose(LAB_FILES, 'logs', '--tail=30')
        compose(LAB_FILES, 'down', '--volumes', '--remove-orphans')


if __name__ == '__main__':
    main()
