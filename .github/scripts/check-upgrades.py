"""Rehearse published installations and cross-version storage on disposable data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import time

_ROOT = Path(__file__).resolve().parents[2]
_BASELINES = ('0.0.78', '0.0.79', '0.0.82')
_IMAGE = 'decryptus/monit-docker:'
_CANDIDATE_IMAGE = 'monit-upgrade-candidate:local'


def run(args, cwd, env=None, timeout=600):
    return subprocess.check_output(list(map(str, args)), cwd=cwd, env=env,
                                   stderr=subprocess.STDOUT, text=True, timeout=timeout)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend', choices=('pip', 'docker'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--integration', action='store_true', help='requires a disposable Docker daemon')
    options = parser.parse_args()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = dict(backend=options.backend, integration=options.integration,
                  python=sys.version, platform=platform.platform(),
                  candidate_commit=run(['git', 'rev-parse', 'HEAD'], _ROOT).strip(),
                  baselines=list(_BASELINES), phases=[], result='running')
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    environment.pop('MONIT_DOCKER_CONFIG', None)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='monit-upgrades-') as temporary:
            work = Path(temporary)
            for name in ('upgrade-fixture.py',):
                shutil.copyfile(_ROOT / '.github/scripts' / name, work / name)
            shutil.copyfile(_ROOT / 'tests/test_docker_integration.py', work / 'test_docker_integration.py')
            runners = {}
            artifacts = {}
            if options.backend == 'pip':
                for version in (*_BASELINES, 'candidate'):
                    venv = work / ('venv-' + version)
                    run([sys.executable, '-m', 'venv', venv], work)
                    python = venv / 'bin/python'
                    run([python, '-m', 'pip', 'install', 'setuptools', 'wheel'], work)
                    target = str(_ROOT) if version == 'candidate' else 'monit-docker==' + version
                    install_report = work / ('install-' + version + '.json')
                    run([python, '-m', 'pip', 'install', '--report', install_report, target], work)
                    shutil.copyfile(install_report, output / install_report.name)
                    run([python, '-m', 'pip', 'check'], work)
                    artifacts[version] = run([python, '-m', 'pip', 'freeze'], work).splitlines()
                    runners[version] = [str(python)]
            else:
                report['docker'] = run(['docker', 'version'], work)
                for version in (*_BASELINES, 'candidate'):
                    image = _CANDIDATE_IMAGE if version == 'candidate' else _IMAGE + version
                    if version == 'candidate':
                        run(['docker', 'build', '-t', image, _ROOT], work, timeout=900)
                    else:
                        run(['docker', 'pull', image], work)
                    metadata = json.loads(run(['docker', 'image', 'inspect', image], work))[0]
                    artifacts[version] = dict(image=image, id=metadata['Id'],
                                              digests=metadata.get('RepoDigests', []))
                    runners[version] = ['docker', 'run', '--rm', '--network', 'host',
                        '--user', '%s:%s' % (os.getuid(), os.getgid()),
                        '--group-add', str(os.stat('/var/run/docker.sock').st_gid),
                        '-v', str(work) + ':' + str(work), '-w', str(work),
                        '-v', '/var/run/docker.sock:/var/run/docker.sock',
                        '-e', 'MONIT_DOCKER_INTEGRATION=1', '-e', 'LOGNAME=upgrade-rehearsal',
                        '-e', 'USER=upgrade-rehearsal', '--entrypoint', 'python', image]
            report['artifacts'] = artifacts

            def invoke(version, arguments, label):
                text = run(runners[version] + arguments, work, environment, timeout=300)
                report['phases'].append(dict(version=version, check=label, result='passed'))
                (output / (label + '-' + version + '.log')).write_text(text)

            for version in (*_BASELINES, 'candidate'):
                invoke(version, ['-m', 'monit_docker', '--help'], 'fresh-cli')
                actual = run(runners[version] + ['-c', 'from importlib.metadata import version; print(version("monit-docker"))'], work).strip()
                if version != 'candidate' and actual != version:
                    raise RuntimeError('installed version mismatch')
                if options.integration:
                    environment['MONIT_DOCKER_INTEGRATION'] = '1'
                    invoke(version, ['-m', 'unittest', '-v',
                        'test_docker_integration.DockerIntegrationTests.test_serve_collects_real_container_and_stops_on_sigterm',
                        'test_docker_integration.DockerIntegrationTests.test_cron_cli_dry_run_and_persistent_cooldown_across_processes'], 'installed-cron-serve')
            for version in _BASELINES:
                data = work / ('data-' + version)
                backup = work / ('backup-' + version)
                probe = str(work / 'upgrade-fixture.py')
                invoke(version, [probe, 'seed', str(data)], 'seed')
                shutil.copytree(data, backup)
                original = hashes(backup)
                invoke('candidate', [probe, 'read', str(data)], 'upgrade-read-' + version)
                invoke('candidate', [probe, 'advance', str(data)], 'upgrade-write-' + version)
                invoke(version, [probe, 'read', str(data)], 'rollback-current-data')
                invoke(version, [probe, 'read', str(backup)], 'rollback-backup')
                if hashes(backup) != original:
                    raise RuntimeError('backup was changed')
            report['result'] = 'passed'
    except BaseException as error:
        report['result'] = 'failed'
        report['error'] = str(error)
        if isinstance(error, subprocess.CalledProcessError):
            (output / 'failure.log').write_text(error.output or '')
            print(error.output or '', file=sys.stderr)
        raise
    finally:
        report['seconds'] = round(time.monotonic() - started, 2)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
