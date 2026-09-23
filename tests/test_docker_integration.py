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
from monit_docker.core import MonitoringEngine
from monit_docker.domain.errors import MonitoringError


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

    def create_container(self):
        obj = self.client.containers.run('alpine:3.20', ['sleep', '120'],
                                          name=self.name, detach=True)
        self.objects.append(obj)
        return obj

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
