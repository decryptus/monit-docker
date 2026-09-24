"""Opt-in checks against a real Docker daemon; only test-owned containers act.

Run after pulling alpine:3.20:
MONIT_DOCKER_INTEGRATION=1 python -m unittest discover -s tests -p test_docker_integration.py -v
"""

import os
import json
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import unittest
import uuid
from unittest.mock import Mock

import docker
from docker.errors import NotFound

from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.state import LocalState
from monit_docker.core import MonitoringEngine
from monit_docker.core.policy import RestartPolicy
from monit_docker.domain.errors import ActionRejected, MonitoringError


@unittest.skipUnless(os.environ.get('MONIT_DOCKER_INTEGRATION') == '1', 'requires opt-in Docker daemon')
class DockerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = docker.from_env(timeout=15)
        self.addCleanup(self.client.close)
        self.objects = []
        self.addCleanup(self.remove_containers)
        self.name = 'monit-engine-test-' + uuid.uuid4().hex
        self.selector = ContainerSelector(selectors={'name': [self.name]})
        self.collector = DockerCollector(lambda: docker.from_env(timeout=15), self.selector)
        self.executor = Mock(wraps=DockerActionExecutor(self.collector))
        self.engine = MonitoringEngine(self.collector, self.executor)

    def remove_containers(self):
        for obj in self.objects:
            try:
                obj.remove(force=True)
            except NotFound:
                pass

    def create_container(self, labels=None, healthcheck=None):
        obj = self.client.containers.run('alpine:3.20', ['sleep', '120'],
                                          name=self.name, detach=True, labels=labels or {}, healthcheck=healthcheck)
        self.objects.append(obj)
        return obj

    def test_healthcheck_transitions_and_stopped_state(self):
        obj = self.create_container(healthcheck={'test': ['CMD', 'test', '-f', '/tmp/healthy'],
                                                'interval': 1000000000, 'timeout': 1000000000, 'retries': 1})

        def wait_health(expected):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                obj.reload()
                if obj.attrs['State'].get('Health', {}).get('Status') == expected:
                    return
                time.sleep(0.2)
            self.fail('Docker did not reach health state %s' % expected)

        wait_health('unhealthy')
        rule = RuleParser().parse('health == unhealthy ? (true)')
        result = self.engine.run_once(rules=(rule,), resources=('health',))
        self.assertEqual(result.snapshots[0].health, 'unhealthy')
        self.assertEqual(len(result.actions), 1)
        self.assertEqual(obj.exec_run(['touch', '/tmp/healthy']).exit_code, 0)
        wait_health('healthy')
        result = self.engine.run_once(rules=(rule,), resources=('health',))
        self.assertEqual(result.snapshots[0].health, 'healthy')
        self.assertEqual(result.actions, ())
        obj.stop(timeout=1)
        result = self.engine.run_once(rules=(rule,), resources=('health',))
        self.assertEqual(result.snapshots[0].health, 'unknown')
        self.assertEqual(result.actions, ())

    def test_restart_budget_survives_fresh_cycles_against_docker(self):
        obj = self.create_container()
        rule = RuleParser().parse('restart')
        with tempfile.TemporaryDirectory() as directory:
            for cycle in range(3):
                decisions = []
                with LocalState(os.path.join(directory, 'state.json')) as state:
                    policy = RestartPolicy(state, (rule,), 0, max_restarts=1)
                    result = self.engine.run_once(rules=(rule,), resources=('health',),
                                                  action_policy=policy, on_action=decisions.append)
                self.assertEqual(decisions[0].status, 'executed' if cycle == 0 else 'restart-limit')
                self.assertEqual(result.snapshots[0].id, obj.id)
                self.assertEqual(result.snapshots[0].restart_attempts, 1)
            self.executor.execute.assert_called_once()

    def test_read_only_root_bind_mount_and_writable_tmpfs_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.client.containers.run('alpine:3.20', ['sleep', '120'], name=self.name,
                detach=True, read_only=True, tmpfs={'/writable': 'rw,size=1m'},
                volumes={directory: {'bind': '/bound', 'mode': 'ro'}})
            self.objects.append(obj)
            self.assertEqual(obj.exec_run(['ln', '-s', '/bound', '/writable/link']).exit_code, 0)
            groups = {'data': {'paths': ['/etc', '/writable', '/bound', '/writable/link']}}
            collector = DockerCollector(lambda: docker.from_env(timeout=15), self.selector, groups)
            engine = MonitoringEngine(collector, DockerActionExecutor(collector))
            rule = RuleParser(dir_groups=groups).parse('fs_mode[data] == ro ? (true)')
            result = engine.run_once(rules=(rule,), resources=('fs_mode[data]',))
            self.assertEqual([s.fs_mode for s in result.snapshots[0].filesystems], ['ro', 'rw', 'ro', 'ro'])
            self.assertEqual(len(result.actions), 1)

    def test_busybox_filesystem_groups_and_missing_paths(self):
        obj = self.create_container()
        groups = {'system': {'paths': ['/', '/tmp']}, 'shared': {'paths': ['/dev/shm']}}
        collector = DockerCollector(lambda: docker.from_env(timeout=15), self.selector, groups)
        engine = MonitoringEngine(collector, DockerActionExecutor(collector))
        result = engine.run_once(resources=('disk_percent[system]', 'inode_percent[shared]'))
        samples = result.snapshots[0].filesystems
        self.assertEqual([sample.path for sample in samples], ['/', '/tmp', '/dev/shm'])
        for sample in samples:
            self.assertGreater(sample.disk_total, 0)
            self.assertGreaterEqual(sample.disk_percent, 0)
            self.assertLessEqual(sample.disk_percent, 100)
        self.assertEqual(samples[0].disk_total, samples[1].disk_total)
        self.assertIsNotNone(samples[2].inode_percent)
        rule = RuleParser(dir_groups=groups).parse('disk_percent[system] >= 0 ? (true)')
        self.assertEqual(len(engine.run_once(rules=(rule,)).actions), 1)
        collector.dir_groups['system'] = ('/monit-does-not-exist',)
        with self.assertRaisesRegex(MonitoringError, '/monit-does-not-exist'):
            engine.run_once(resources=('disk_percent[system]',))
        obj.reload()
        self.assertEqual(obj.status, 'running')

    def test_protected_container_allows_automatic_rules_but_no_manual_commands(self):
        obj = self.create_container(labels={'monit-docker.protected': 'true'})
        initial = self.engine.run_once(resources=('cpu_percent',))
        self.assertTrue(initial.snapshots[0].manual_actions_protected)
        self.assertEqual(initial.snapshots[0].id, obj.id)
        self.assertIsNotNone(initial.snapshots[0].cpu_percent)
        claim = Mock(return_value=True)
        for command in ('stop', 'restart'):
            with self.assertRaisesRegex(ActionRejected, 'container_protected'):
                self.engine.run_manual_action(obj.id, command, claim)
        self.engine.run_once(rules=(RuleParser().parse('stop'),))
        obj.reload()
        self.assertEqual(obj.status, 'exited')
        with self.assertRaisesRegex(ActionRejected, 'container_protected'):
            self.engine.run_manual_action(obj.id, 'start', claim)
        claim.assert_not_called()
        self.engine.run_once(rules=(RuleParser().parse('start'),))
        obj.reload()
        self.assertEqual(obj.status, 'running')

    def test_manual_actions_use_fresh_selection_and_exact_container_id(self):
        obj = self.create_container()
        self.engine.run_manual_action(obj.id, 'stop', lambda _: True)
        obj.reload()
        self.assertEqual(obj.status, 'exited')
        self.engine.run_manual_action(obj.id, 'start', lambda _: True)
        obj.reload()
        self.assertEqual(obj.status, 'running')
        self.engine.run_manual_action(obj.id, 'restart', lambda _: True)
        obj.reload()
        self.assertEqual(obj.status, 'running')
        original_id = obj.id
        obj.remove(force=True)
        replacement = self.create_container()
        from monit_docker.domain.errors import ActionRejected
        with self.assertRaisesRegex(ActionRejected, 'not_selected'):
            self.engine.run_manual_action(original_id, 'stop', lambda _: True)
        replacement.reload()
        self.assertEqual(replacement.status, 'running')

    def test_sampling_and_actions_across_repeated_cycles_and_replacement(self):
        first = self.create_container()
        rule = RuleParser().parse('status == running ? (true)')
        initial = self.engine.run_once(rules=(rule,), resources=('cpu_percent', 'mem_usage'))
        self.assertEqual(initial.snapshots[0].id, first.id)
        self.assertIsNotNone(initial.snapshots[0].cpu_percent)
        self.assertIsNotNone(initial.snapshots[0].mem_usage)
        self.assertEqual(len(initial.actions), 1)
        repeated = self.engine.run_once(resources=('cpu_percent',))
        self.assertEqual(repeated.snapshots[0].id, first.id)
        first.remove(force=True)
        replacement = self.create_container()
        following = self.engine.run_once(resources=('cpu_percent',))
        self.assertEqual(following.snapshots[0].id, replacement.id)
        self.assertNotEqual(following.snapshots[0].id, first.id)
        self.assertIsNone(self.collector.client)

    def test_failed_exec_prevents_following_action(self):
        self.create_container()
        parser = RuleParser()
        with self.assertRaises(MonitoringError) as error:
            self.engine.run_once(rules=(parser.parse('(false)'), parser.parse('restart')))
        self.assertEqual(error.exception.code, 116)
        self.assertEqual(self.executor.execute.call_count, 1)
        self.assertIsNone(self.collector.client)

    def test_serve_collects_real_container_and_stops_on_sigterm(self):
        self.create_container()
        environment = dict(os.environ)
        environment.pop('MONIT_DOCKER_CONFIG', None)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as errors:
            command = [sys.executable, '-m', 'monit_docker', '-c', directory + '/absent.yml',
                       '--logfile', directory + '/absent/log', '--runtimedir', '',
                       '--name', self.name, 'serve', '--port', str(port),
                       '--interval', '0.1', '--rsc', 'mem_usage']
            process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL, stderr=errors)
            try:
                deadline = time.monotonic() + 20
                data = None
                while time.monotonic() < deadline and process.poll() is None:
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:%s/v1/status' % port, timeout=1) as response:
                            data = json.load(response)
                        if data['ready']:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.1)
                errors.seek(0)
                self.assertTrue(data and data['ready'], errors.read().decode())
                self.assertEqual(data['containers'][0]['name'], self.name)
                self.assertIsNotNone(data['containers'][0]['mem_usage'])
                with urllib.request.urlopen('http://127.0.0.1:%s/metrics' % port, timeout=2) as response:
                    self.assertIn(b'monit_docker_container_memory_usage_bytes{', response.read())
                process.send_signal(signal.SIGTERM)
                self.assertEqual(process.wait(timeout=20), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_cron_cli_dry_run_and_persistent_cooldown_across_processes(self):
        obj = self.create_container()
        environment = dict(os.environ)
        environment.pop('MONIT_DOCKER_CONFIG', None)
        with tempfile.TemporaryDirectory() as directory:
            state = directory + '/cron.json'
            command = [sys.executable, '-m', 'monit_docker', '-c', directory + '/absent.yml',
                       '--logfile', directory + '/absent/log', '--runtimedir', '',
                       '--name', self.name, 'cron', '--state-file', state,
                       '--cmd', '(sh -c "echo run >> /tmp/cron-count")']

            def invoke(extra=()):
                result = subprocess.run(command + list(extra), env=environment,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            self.assertIn('"status": "dry-run"', invoke(['--dry-run']))
            self.assertFalse(os.path.exists(state))
            self.assertEqual(obj.exec_run(['test', '!', '-e', '/tmp/cron-count']).exit_code, 0)
            self.assertIn('"status": "executed"', invoke())
            self.assertIn('"status": "cooldown"', invoke())
            self.assertEqual(obj.exec_run(['cat', '/tmp/cron-count']).output, b'run\n')

    def test_cli_propagates_actual_process_status_and_stops_following_exec(self):
        obj = self.create_container()
        environment = dict(os.environ)
        environment.pop('MONIT_DOCKER_CONFIG', None)
        with tempfile.TemporaryDirectory() as directory:
            base = [sys.executable, '-m', 'monit_docker', '-c', directory + '/absent.yml',
                    '--logfile', directory + '/absent/log', '--runtimedir', '',
                    '--name', self.name, 'monit']
            for code, propagate in ((0, True), (1, True), (42, True), (255, True), (42, False)):
                with self.subTest(code=code, propagate=propagate):
                    flags = ['--propagate-exit-code'] if propagate else []
                    command = base + flags + ['--cmd', '(sh -c "exit %s")' % code]
                    if code:
                        command += ['--cmd', '(touch /tmp/monit-unexpected-action)']
                    result = subprocess.run(command, env=environment, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, timeout=30)
                    expected = code if propagate or code == 0 else 116
                    self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(obj.exec_run(['test', '!', '-e', '/tmp/monit-unexpected-action']).exit_code, 0)


if __name__ == '__main__':
    unittest.main()
