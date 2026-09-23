"""Exercise published agent/UI images on a disposable Docker Compose project.

Requires Docker Compose v2, OpenSSL, Node and Playwright Chromium. Credentials
stay in a temporary directory; only screenshots and a sanitized report survive.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from unittest.mock import patch


_ROOT              = Path(__file__).resolve().parents[2]
_COMPOSE_FILES     = ('compose.yaml', 'compose.actions.yaml', 'prepare.py')
_RELEASE_PATTERN   = re.compile(r'[0-9]+\.[0-9]+\.[0-9]+')
_DEFAULT_RELEASE   = '0.0.65'
_ORIGIN            = 'https://localhost:18443'
_USERNAME          = 'demo'
_DEMO_IMAGE        = 'alpine:3.20'
_DEMO_COMMAND      = ('sleep', '900')
_DEMO_LABELS       = {'monit-docker.fixture': 'ui-release-acceptance'}
_DEMO_NETWORKS     = ('agent',)
_SERVICE_NAMES     = ('monit-docker', 'ui', 'demo')
_SERVE_OPTIONS     = ('serve', '--bind', '0.0.0.0', '--interval', '1')
_ACTION_OPTIONS    = ('--allow-actions', '--action-origin', _ORIGIN,
                      '--action-token-file', '/run/secrets/action-token',
                      '--state-file', '/var/lib/monit-docker/state.json',
                      '--action-cooldown', '1')
_PULL_OPTIONS      = ('pull', '--quiet')
_UP_OPTIONS        = ('up', '-d', '--no-build')
_DOWN_OPTIONS      = ('down', '--volumes', '--remove-orphans')
_INSPECT_OPTIONS   = ('docker', 'inspect')
_BROWSER_OPTIONS   = ('node', str(_ROOT / 'ui/tests/release.cjs'))


def command(*args, env = None):
    return subprocess.check_output(args, text = True, env = env).strip()


def prepare(directory, password):
    for name in _COMPOSE_FILES:
        shutil.copyfile(_ROOT / 'examples/ui' / name, directory / name)
    spec   = importlib.util.spec_from_file_location('ui_prepare', directory / 'prepare.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = [str(directory / 'prepare.py'), '--actions', '--self-signed']
    with patch.object(sys, 'argv', arguments), \
         patch('builtins.input', return_value = _USERNAME), \
         patch('getpass.getpass', return_value = password):
        module.main()


def wait_for_proxy():
    # Only this temporary localhost certificate is untrusted.
    context  = ssl._create_unverified_context()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(_ORIGIN, context = context, timeout = 2).close()
        except urllib.error.HTTPError as error:
            if error.code == 401:
                return
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError('Published Nginx image did not become ready')


def main():
    release = os.environ.get('MONIT_UI_RELEASE', _DEFAULT_RELEASE)
    if not _RELEASE_PATTERN.fullmatch(release):
        raise ValueError('MONIT_UI_RELEASE must be X.Y.Z')
    output = Path(os.environ.get('UI_SCREENSHOTS', '/tmp/monit-ui-release-results')).resolve()
    output.mkdir(parents = True, exist_ok = True)
    project  = 'monit-ui-release-' + secrets.token_hex(4)
    demo     = project + '-demo'
    password = secrets.token_urlsafe(32)
    report   = dict(release = release, project = project, images = {}, phases = [])
    env      = os.environ.copy()
    env.update(COMPOSE_PROJECT_NAME = project, UI_ORIGIN = _ORIGIN,
               UI_BIND = '127.0.0.1', UI_PORT = '18443')
    command('docker', 'info', '--format', '{{.ServerVersion}}')
    command('docker', 'compose', 'version')
    with tempfile.TemporaryDirectory(prefix = project + '-') as temporary:
        directory = Path(temporary)
        prepare(directory, password)
        serve = ['monit-docker', '--name', demo, *_SERVE_OPTIONS]
        services = {
            'monit-docker': {'image': 'decryptus/monit-docker:' + release, 'command': serve},
            'ui': {'image': 'decryptus/monit-docker-ui:' + release},
            'demo': {'image': _DEMO_IMAGE, 'container_name': demo,
                     'command': _DEMO_COMMAND, 'labels': _DEMO_LABELS,
                     'networks': _DEMO_NETWORKS, 'init': True, 'mem_limit': '64m',
                     'cpus': 0.25, 'pids_limit': 32},
        }
        overlay = directory / 'acceptance.json'
        overlay.write_text(json.dumps(dict(services = services)))
        base = ('docker', 'compose', '-f', str(directory / 'compose.yaml'))
        readonly = (*base, '-f', str(overlay))
        writable = (*base, '-f', str(directory / 'compose.actions.yaml'), '-f', str(overlay))
        try:
            command(*readonly, *_PULL_OPTIONS, env = env)
            command(*readonly, *_UP_OPTIONS, env = env)
            wait_for_proxy()
            ids = {name: command(*readonly, 'ps', '-q', name, env = env) for name in _SERVICE_NAMES}
            inspected = {name: json.loads(command(*_INSPECT_OPTIONS, identifier))[0]
                         for name, identifier in ids.items()}
            assert not inspected['monit-docker']['HostConfig']['PortBindings']
            assert all(mount['Destination'] != '/var/run/docker.sock'
                       for mount in inspected['ui']['Mounts'])
            assert inspected['demo']['Config']['Labels']['monit-docker.fixture'] == _DEMO_LABELS['monit-docker.fixture']
            for name in ('monit-docker', 'ui'):
                actual = inspected[name]['Config']['Image']
                assert actual == services[name]['image']
                image = json.loads(command('docker', 'image', 'inspect', actual))[0]
                report['images'][name] = dict(tag = actual, digests = image['RepoDigests'])
                assert image['RepoDigests'], 'Expected a pulled image with a registry digest'
            version = command(*readonly, 'exec', '-T', 'monit-docker', 'python', '-c',
                              'from importlib.metadata import version; print(version("monit-docker"))', env = env)
            assert version == release
            credentials = directory / 'browser.json'
            credentials.write_text(json.dumps(dict(origin = _ORIGIN, username = _USERNAME,
                                                     password = password, demo_id = ids['demo'],
                                                     demo_name = demo, output = str(output))))
            credentials.chmod(0o600)
            for phase in ('readonly', 'actions'):
                if phase == 'actions':
                    services['monit-docker']['command'] = serve + list(_ACTION_OPTIONS)
                    overlay.write_text(json.dumps(dict(services = services)))
                    command(*writable, *_UP_OPTIONS, env = env)
                    # Recreating the agent changes its IP; refresh Nginx's static DNS resolution.
                    command(*writable, 'restart', 'ui', env = env)
                    wait_for_proxy()
                subprocess.run((*_BROWSER_OPTIONS, str(credentials), phase), env = env, check = True)
                report['phases'].append(phase)
            report['result'] = 'passed'
            (output / 'release-report.json').write_text(json.dumps(report, indent = 2) + '\n')
            print('Published UI acceptance passed: read-only, authenticated actions, desktop and mobile.')
        except Exception:
            subprocess.run((*writable, 'logs', '--tail', '60'), env = env, check = False)
            raise
        finally:
            subprocess.run((*writable, *_DOWN_OPTIONS), env = env, check = True)


if __name__ == '__main__':
    main()
