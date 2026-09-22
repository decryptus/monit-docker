"""Cron regression tests: real files/process locks, simulated Docker actions."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import monit_docker
from monit_docker import cli
from monit_docker.adapters.state import LocalState
from monit_docker.domain.errors import MonitoringError
import test_monit_docker as legacy


class LocalStateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / 'job.json'
        self.key = 'a' * 64

    def child(self, body):
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(monit_docker.__file__).resolve().parent.parent)
        return subprocess.run([sys.executable, '-c',
            'import os, sys\nfrom monit_docker.adapters.state import LocalState\n'
            'from monit_docker.domain.errors import MonitoringError\n' + body,
            str(self.path)], env=environment, cwd=str(self.path.parent),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)

    def test_another_process_cannot_acquire_lock_and_retry_can(self):
        body = ('try:\n with LocalState(sys.argv[1]): pass\n'
                'except MonitoringError as error: sys.exit(error.code)\n')
        with LocalState(str(self.path)):
            result = self.child(body)
            self.assertEqual(result.returncode, 117, result.stderr)
        self.assertEqual(self.child(body).returncode, 0)
        self.assertTrue(Path(str(self.path) + '.lock').exists())

    def test_abrupt_process_exit_releases_lock_and_preserves_reservation(self):
        result = self.child("with LocalState(sys.argv[1]) as state:\n"
                            " state.reserve('a' * 64, 100, 300)\n os._exit(0)\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        with LocalState(str(self.path)) as state:
            self.assertFalse(state.reserve(self.key, 101, 300))
            self.assertFalse(state.reserve(self.key, 50, 300))
            self.assertTrue(state.reserve(self.key, 400, 300))

    def test_dry_run_preserves_bytes_and_mtime_and_missing_state(self):
        with LocalState(str(self.path)) as state:
            state.reserve(self.key, 100, 300, read_only=True)
        self.assertFalse(self.path.exists())
        with LocalState(str(self.path)) as state:
            state.reserve(self.key, 100, 300)
        before = (self.path.read_bytes(), self.path.stat().st_mtime_ns)
        with LocalState(str(self.path)) as state:
            self.assertFalse(state.reserve(self.key, 200, 300, read_only=True))
            self.assertTrue(state.reserve(self.key, 400, 300, read_only=True))
            self.assertFalse(state.reserve(self.key, 401, 300, read_only=True))
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_atomic_write_failure_preserves_existing_state_and_releases_lock(self):
        with LocalState(str(self.path)) as state:
            state.reserve(self.key, 100, 300)
        before = self.path.read_bytes()
        with self.assertRaises(MonitoringError) as error:
            with LocalState(str(self.path)) as state, patch(
                    'monit_docker.adapters.state.os.rename', side_effect=OSError('disk full')):
                state.reserve('b' * 64, 200, 300)
        self.assertEqual(error.exception.code, 118)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.monit-state-*')), [])
        with LocalState(str(self.path)) as state:
            self.assertFalse(state.reserve(self.key, 200, 300))

    def test_corrupt_or_unknown_state_is_rejected_without_resetting(self):
        cases = ['not json', '{}', '[]',
                 '{"version":2,"cooldowns":{}}',
                 '{"version":true,"cooldowns":{}}',
                 json.dumps({'version': 1, 'cooldowns': {self.key: float('nan')}}),
                 json.dumps({'version': 1, 'cooldowns': {self.key: -1}}),
                 json.dumps({'version': 1, 'cooldowns': {self.key: True}}),
                 '{"version":1,"cooldowns":{"bad":123}}']
        for content in cases:
            with self.subTest(content=content):
                self.path.write_text(content)
                with self.assertRaises(MonitoringError) as error:
                    with LocalState(str(self.path)):
                        self.fail('invalid state accepted')
                self.assertEqual(error.exception.code, 118)
                self.assertEqual(self.path.read_text(), content)

    def test_state_symlink_is_rejected_without_touching_target(self):
        target = self.path.parent / 'target'
        target.write_text('private')
        self.path.symlink_to(target)
        with self.assertRaises(MonitoringError) as error:
            with LocalState(str(self.path)):
                pass
        self.assertEqual(error.exception.code, 118)
        self.assertEqual(target.read_text(), 'private')

    def test_expired_entries_pruned_and_new_file_private(self):
        with LocalState(str(self.path)) as state:
            state.reserve(self.key, 100, 10)
            state.reserve('b' * 64, 110, 300)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['cooldowns'], {'b' * 64: 410})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)


class CronCliTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    def cron(self, *args):
        return self.invoke('cron', '--state-file', str(Path(self.temp.name) / 'job.json'), *args)

    def test_cooldown_survives_new_cli_instances_until_exact_expiry(self):
        obj = self.client.containers.list.return_value[0]
        with patch('monit_docker.core.policy.time.time', return_value=100):
            self.assertEqual(self.cron('--cmd', 'restart'), 0)
        with patch('monit_docker.core.policy.time.time', return_value=399), patch.object(cli.sys.stdout, 'write') as write:
            self.assertEqual(self.cron('--cmd', 'restart'), 0)
            self.assertEqual(json.loads(write.call_args[0][0])['status'], 'cooldown')
        obj.restart.assert_called_once_with()
        with patch('monit_docker.core.policy.time.time', return_value=400):
            self.assertEqual(self.cron('--cmd', 'restart'), 0)
        self.assertEqual(obj.restart.call_count, 2)

    def test_dry_run_never_calls_actions_or_changes_persistent_state(self):
        obj = self.client.containers.list.return_value[0]
        with patch.object(cli.sys.stdout, 'write') as write:
            self.assertEqual(self.cron('--dry-run', '--cmd', 'restart',
                                      '--cmd-if', 'mem_percent > 60 ? (touch /tmp/nope)'), 0)
            decisions = [json.loads(call[0][0]) for call in write.call_args_list]
        self.assertEqual([d['status'] for d in decisions], ['dry-run', 'dry-run'])
        obj.restart.assert_not_called()
        obj.exec_run.assert_not_called()
        self.assertFalse((Path(self.temp.name) / 'job.json').exists())
        self.assertEqual(self.cron('--cmd', 'restart'), 0)
        obj.restart.assert_called_once_with()

    def test_monit_dry_run_is_available_without_state(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.invoke('monit', '--dry-run', '--cmd', 'restart'), 0)
        obj.restart.assert_not_called()

    def test_corrupt_state_and_contention_prevent_docker_connection(self):
        path = Path(self.temp.name) / 'job.json'
        path.write_text('broken')
        with patch.object(cli.docker, 'from_env') as connect:
            self.assertEqual(self.cron('--cmd', 'restart'), 118)
            connect.assert_not_called()
        path.unlink()
        with LocalState(str(path)), patch.object(cli.docker, 'from_env') as connect:
            self.assertEqual(self.cron('--cmd', 'restart'), 117)
            connect.assert_not_called()

    def test_failed_reservation_prevents_action(self):
        obj = self.client.containers.list.return_value[0]
        with patch('monit_docker.adapters.state.os.rename', side_effect=OSError('disk full')):
            self.assertEqual(self.cron('--cmd', 'restart'), 118)
        obj.restart.assert_not_called()
        self.client.api.close.assert_called_once_with()

    def test_failed_rule_is_not_immediately_retried_or_resumed(self):
        from docker.models.containers import ExecResult
        obj = self.client.containers.list.return_value[0]
        obj.exec_run.return_value = ExecResult(42, b'failed')
        self.conf.write_text('commands:\n  sequence:\n    exec: [restart, "(false)", pause]\n')
        self.assertEqual(self.cron('--propagate-exit-code', '--cmd', '@sequence'), 42)
        self.assertEqual(self.cron('--propagate-exit-code', '--cmd', '@sequence'), 0)
        obj.restart.assert_called_once_with()
        obj.exec_run.assert_called_once_with('false')
        obj.pause.assert_not_called()

    def test_replacement_container_and_changed_alias_get_fresh_cooldowns(self):
        obj = self.client.containers.list.return_value[0]
        self.conf.write_text('commands:\n  action:\n    exec: [restart]\n')
        self.assertEqual(self.cron('--cmd', '@action'), 0)
        self.assertEqual(self.cron('--cmd', '@action'), 0)
        obj.restart.assert_called_once_with()
        obj.id = 'replacement-id'
        self.assertEqual(self.cron('--cmd', '@action'), 0)
        self.assertEqual(obj.restart.call_count, 2)
        self.conf.write_text('commands:\n  action:\n    exec: [pause]\n')
        self.assertEqual(self.cron('--cmd', '@action'), 0)
        obj.pause.assert_called_once_with()

    def test_unmatched_rules_do_not_reserve_and_zero_disables_cooldown(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.cron('--cmd-if', 'status == exited ? restart'), 0)
        self.assertFalse((Path(self.temp.name) / 'job.json').exists())
        self.assertEqual(self.cron('--cooldown', '0', '--cmd', 'restart'), 0)
        self.assertEqual(self.cron('--cooldown', '0', '--cmd', 'restart'), 0)
        self.assertEqual(obj.restart.call_count, 2)

    def test_invalid_options_fail_before_docker(self):
        for args in [(), ('--cooldown', '-1', '--cmd', 'restart'),
                     ('--cooldown', 'nan', '--cmd', 'restart'),
                     ('--cooldown', 'inf', '--cmd', 'restart'),
                     ('--rsc', 'status', '--cmd', 'restart')]:
            with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                self.cron(*args)
            self.assertEqual(error.exception.code, 2)
        self.client.containers.list.assert_not_called()


if __name__ == '__main__':
    unittest.main()
