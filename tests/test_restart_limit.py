"""Bounded automatic restarts across cycles, aliases, failures and explicit rearm."""

import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from docker.errors import APIError
from monit_docker import cli
from monit_docker.adapters.state import LocalState
from monit_docker.core.policy import restart_key
from monit_docker.domain.errors import MonitoringError
from monit_docker.outputs.prometheus import render_metrics
from monit_docker.service import MonitorService
import test_monit_docker as legacy


class RestartCliTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    @property
    def path(self):
        return Path(self.temp.name) / 'state.json'

    def cron(self, *args):
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            code = self.invoke('cron', '--state-file', str(self.path), '--cooldown', '0', *args)
        return code, [json.loads(line) for line in output.getvalue().splitlines()]

    def test_three_attempts_then_block_across_cli_instances_and_alias_changes(self):
        obj = self.client.containers.list.return_value[0]
        for _ in range(3):
            self.assertEqual(self.cron('--cmd', 'restart')[1][0]['status'], 'executed')
        self.conf.write_text('commands:\n  fix:\n    exec: ["(true)", restart, pause]\n')
        for _ in range(3):
            code, decisions = self.cron('--cmd', '@fix', '--cmd-if', 'status == running ? restart')
            self.assertEqual(code, 0)
            self.assertEqual([d['status'] for d in decisions], ['restart-limit'] * 4)
        self.assertEqual(obj.restart.call_count, 3)
        obj.exec_run.assert_not_called()
        obj.pause.assert_not_called()
        events = [json.loads(line) for line in (self.path.parent / 'audit/events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('reason') == 'restart-limit' for e in events))
        # Unrelated actions remain available and a replacement ID has its own budget.
        self.assertEqual(self.cron('--cmd', 'pause')[1][0]['status'], 'executed')
        obj.id = 'replacement'
        self.assertEqual(self.cron('--cmd', 'restart')[1][0]['status'], 'executed')

    def test_failures_consume_budget_but_blocked_calls_never_reach_docker(self):
        obj = self.client.containers.list.return_value[0]
        obj.restart.side_effect = APIError('unavailable')
        for _ in range(2):
            self.assertEqual(self.cron('--max-restarts', '2', '--cmd', 'restart')[0], 116)
        self.assertEqual(self.cron('--max-restarts', '2', '--cmd', 'restart')[1][0]['status'], 'restart-limit')
        self.assertEqual(obj.restart.call_count, 2)

    def test_multiple_actions_are_preflighted_and_dry_run_preserves_state(self):
        obj = self.client.containers.list.return_value[0]
        self.conf.write_text('commands:\n  twice:\n    exec: [restart, restart]\n')
        self.assertEqual(self.cron('--cmd', '@twice')[0], 0)
        self.assertEqual(obj.restart.call_count, 2)
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        _, decisions = self.cron('--dry-run', '--cmd', 'restart', '--cmd', '@twice')
        self.assertEqual([d['status'] for d in decisions], ['dry-run', 'restart-limit', 'restart-limit'])
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)
        self.assertEqual(self.cron('--cmd', '@twice')[1][0]['status'], 'restart-limit')
        self.assertEqual(obj.restart.call_count, 2)
        self.assertEqual(self.cron('--cmd', 'restart')[1][0]['status'], 'executed')

    def test_healthy_stopped_and_absent_observations_do_not_rearm(self):
        obj = self.client.containers.list.return_value[0]
        obj.attrs['Config'] = {'Healthcheck': {'Test': ['CMD', 'true']}}
        rule = ('--max-restarts', '1', '--cmd-if', 'health == unhealthy ? restart')
        obj.attrs['State']['Health'] = {'Status': 'unhealthy'}
        self.cron(*rule)
        obj.attrs['State']['Health']['Status'] = 'healthy'
        self.assertEqual(self.cron(*rule)[1], [])
        obj.status = 'exited'
        self.assertEqual(self.cron(*rule)[1], [])
        self.client.containers.list.return_value = []
        self.assertEqual(self.cron(*rule)[0], 114)
        self.client.containers.list.return_value = [obj]
        obj.status = 'running'
        obj.attrs['State']['Health']['Status'] = 'unhealthy'
        self.assertEqual(self.cron(*rule)[1][0]['status'], 'restart-limit')
        obj.restart.assert_called_once_with()

    def test_failed_budget_write_prevents_action(self):
        original = LocalState._save
        def save(state, entries, observations=None, restarts=None):
            if restarts is not None:
                raise MonitoringError(118, 'disk full')
            return original(state, entries, observations, restarts)
        with patch.object(LocalState, '_save', save):
            self.assertEqual(self.cron('--cmd', 'restart')[0], 118)
        self.client.containers.list.return_value[0].restart.assert_not_called()

    def test_reset_is_offline_exact_audited_and_preserves_other_state(self):
        obj = self.client.containers.list.return_value[0]
        obj.id = 'a' * 64
        self.cron('--max-restarts', '1', '--cmd', 'restart')
        with LocalState(str(self.path)) as state:
            state.reserve_restart(restart_key('b' * 64), 3)
            state.replace_observations({'c' * 64: [1, 2]})
        before = json.loads(self.path.read_text())
        with patch.object(cli.docker, 'from_env') as connect, patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.invoke('restart-reset', '--state-file', str(self.path), '--container-id', obj.id), 0)
            connect.assert_not_called()
            self.assertEqual(self.invoke('restart-reset', '--state-file', str(self.path), '--container-id', obj.id), 110)
        after = json.loads(self.path.read_text())
        self.assertEqual(after['cooldowns'], before['cooldowns'])
        self.assertEqual(after['observations'], before['observations'])
        self.assertEqual(after['restarts'], {restart_key('b' * 64): 1})
        events = [json.loads(line) for line in (self.path.parent / 'audit/events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('action') == 'restart-reset' and e.get('result') == 'succeeded' for e in events))
        self.assertEqual(self.cron('--max-restarts', '1', '--cmd', 'restart')[1][0]['status'], 'executed')

    def test_service_reports_latched_budget_and_continues_monitoring(self):
        base = ['monit-docker', '-c', str(self.conf), 'serve', '--state-file', str(self.path),
                '--max-restarts', '1', '--cooldown', '0', '--rsc', 'health', '--cmd', 'restart']
        with patch('sys.argv', base):
            command = cli.MonitDockerSubCmdServe(cli.argv_parse_check())
        monitor = MonitorService(command._cycle)
        monitor.run_cycle()
        monitor.run_cycle()
        data = monitor.status()
        self.assertTrue(data['ready'])
        self.assertEqual(data['actions']['restart-limit'], 1)
        self.assertEqual(data['containers'][0]['restart_attempts'], 1)
        self.assertEqual(data['containers'][0]['restart_limit'], 1)
        self.assertIn('monit_docker_container_restart_blocked{id="demo",name="demo"} 1', render_metrics(data))
        data['ready'] = False
        self.assertNotIn('restart_blocked{id=', render_metrics(data))

    def test_reset_contention_or_unavailable_audit_preserves_budget(self):
        from monit_docker.audit import AuditError
        obj = self.client.containers.list.return_value[0]
        obj.id = 'a' * 64
        self.cron('--cmd', 'restart')
        before = self.path.read_bytes()
        args = ('restart-reset', '--state-file', str(self.path), '--container-id', obj.id)
        with LocalState(str(self.path)):
            self.assertEqual(self.invoke(*args), 117)
        with patch('monit_docker.audit.AuditJournal.record', side_effect=AuditError('unavailable')):
            self.assertEqual(self.invoke(*args), 119)
        self.assertEqual(self.path.read_bytes(), before)

    def test_pending_and_cooldown_do_not_consume_attempts(self):
        args = ('--cooldown', '300', '--trigger-after', '60', '--max-gap', '90',
                '--cmd-if', 'status == running ? restart')
        for now, expected in ((100, 'pending'), (160, 'executed'), (220, 'cooldown')):
            with patch('monit_docker.core.policy.time.time', return_value=now):
                self.assertEqual(self.cron(*args)[1][0]['status'], expected)
        self.assertEqual(json.loads(self.path.read_text())['restarts'], {restart_key('demo'): 1})

    def test_invalid_options_and_ambiguous_reset_ids_fail_before_docker(self):
        for mode in ('cron', 'serve'):
            for value in ('0', '-1', '1.5'):
                with self.assertRaises(SystemExit):
                    self.invoke(mode, '--state-file', str(self.path), '--max-restarts', value, '--cmd', 'restart')
        for value in ('', 'abc', 'name:web', 'A' * 64):
            with self.assertRaises(SystemExit):
                self.invoke('restart-reset', '--state-file', str(self.path), '--container-id', value)
        self.client.containers.list.assert_not_called()


class RestartStateTests(unittest.TestCase):
    def test_failed_reset_write_keeps_budget_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with LocalState(str(path)) as state:
                state.reserve_restart('a' * 64, 1)
            before = path.read_bytes()
            with self.assertRaises(MonitoringError):
                with LocalState(str(path)) as state, patch(
                        'monit_docker.adapters.state.os.rename', side_effect=OSError('disk full')):
                    state.reset_restarts('a' * 64)
            self.assertEqual(path.read_bytes(), before)
            with LocalState(str(path)) as state:
                self.assertFalse(state.reserve_restart('a' * 64, 1))

    def test_migration_retains_cooldowns_and_observations_without_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            for version in (1, 2):
                data = dict(version=version, cooldowns={'a' * 64: 1000})
                if version == 2:
                    data['observations'] = {'b' * 64: [1, 2]}
                path.write_text(json.dumps(data))
                with LocalState(str(path)) as state:
                    state.reserve_restart('c' * 64, 3)
                stored = json.loads(path.read_text())
                self.assertEqual(stored['cooldowns'], data['cooldowns'])
                self.assertEqual(stored['observations'], data.get('observations', {}))
                with LocalState(str(path)) as state:
                    state.replace_observations({'d' * 64: [2, 3]})
                    state.reserve('e' * 64, 10, 30)
                self.assertEqual(json.loads(path.read_text())['version'], 3)
                with LocalState(str(path)) as state:
                    self.assertEqual(state.restarts, {'c' * 64: 1})

    def test_invalid_budget_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            for restarts in ([], {'bad': 1}, {'a' * 64: True}, {'a' * 64: 0},
                             {'a' * 64: -1}, {'a' * 64: 1.5}):
                path.write_text(json.dumps(dict(version=3, cooldowns={}, observations={}, restarts=restarts)))
                before = path.read_bytes()
                with self.assertRaises(MonitoringError):
                    with LocalState(str(path)):
                        self.fail('invalid restart budget accepted')
                self.assertEqual(path.read_bytes(), before)

    def test_crash_after_reservation_retains_budget_and_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with LocalState(str(path)) as state:
                self.assertTrue(state.reserve_restart('a' * 64, 1, read_only=True))
                self.assertFalse(state.reserve_restart('a' * 64, 1, read_only=True))
            self.assertFalse(path.exists())
            result = subprocess.run([sys.executable, '-c',
                'import os, sys\nfrom monit_docker.adapters.state import LocalState\n'
                'with LocalState(sys.argv[1]) as state:\n'
                ' state.reserve_restart("a" * 64, 1)\n os._exit(0)\n', str(path)],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            with LocalState(str(path)) as state:
                self.assertFalse(state.reserve_restart('a' * 64, 1))


if __name__ == '__main__':
    unittest.main()
