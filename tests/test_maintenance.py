"""Finite maintenance preserves observations, manual controls and durable budgets."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monit_docker import cli
from monit_docker.adapters.state import LocalState
from monit_docker.core.policy import restart_key
from monit_docker.domain.errors import ActionRejected, MonitoringError
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService
from monit_docker.outputs.prometheus import render_metrics
import test_monit_docker as legacy

ID = 'a' * 64


class MaintenanceTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    @property
    def path(self):
        return Path(self.temp.name) / 'state.json'

    def change(self, duration, now=100):
        with patch('time.time', return_value=now), patch('sys.stdout', new_callable=io.StringIO):
            return self.invoke('maintenance', '--state-file', str(self.path),
                               '--container-id', ID, '--duration', str(duration))

    def cron(self, now, *args):
        self.client.containers.list.return_value[0].id = ID
        with patch('time.time', return_value=now), patch('sys.stdout', new_callable=io.StringIO) as out:
            code = self.invoke('cron', '--state-file', str(self.path), '--cooldown', '0', *args)
        return code, [json.loads(line) for line in out.getvalue().splitlines()]

    def test_all_automatic_actions_skipped_without_consuming_budget_then_resume(self):
        self.assertEqual(self.change(60), 0)
        for now in (100, 130, 159):
            code, decisions = self.cron(now, '--cmd', 'restart', '--cmd', '(true)', '--cmd', 'pause')
            self.assertEqual(code, 0)
            self.assertEqual([d['status'] for d in decisions], ['maintenance'] * 3)
        obj = self.client.containers.list.return_value[0]
        obj.restart.assert_not_called()
        obj.pause.assert_not_called()
        obj.exec_run.assert_not_called()
        with LocalState(str(self.path)) as state:
            self.assertEqual(state.entries, {})
            self.assertEqual(state.restarts, {})
        self.assertEqual(self.cron(160, '--cmd', 'restart')[1][0]['status'], 'executed')
        obj.restart.assert_called_once_with()
        events = [json.loads(line) for line in (self.path.parent / 'audit/events.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('reason') == 'maintenance' for e in events))
        self.assertEqual(sum(e.get('action') == 'maintenance-expired' and e['event'] == 'completed' for e in events), 1)

    def test_other_container_still_executes_and_existing_budget_is_preserved(self):
        self.change(60)
        with LocalState(str(self.path)) as state:
            state.reserve_restart(restart_key(ID), 1)
        obj = self.client.containers.list.return_value[0]
        obj.id = 'b' * 64
        with patch('time.time', return_value=120), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(self.invoke('cron', '--state-file', str(self.path), '--cmd', 'restart'), 0)
        obj.restart.assert_called_once_with()
        self.assertEqual(self.cron(160, '--max-restarts', '1', '--cmd', 'restart')[1][0]['status'], 'restart-limit')

    def test_cli_is_offline_early_resume_and_dry_run_never_changes_state(self):
        with patch.object(cli.docker, 'from_env') as connect:
            self.assertEqual(self.change(60), 0)
            connect.assert_not_called()
        before = self.path.read_bytes()
        self.assertEqual(self.cron(200, '--dry-run', '--cmd', 'restart')[1][0]['status'], 'dry-run')
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.change(0, now=120), 0)
        self.assertEqual(self.cron(121, '--cmd', 'restart')[1][0]['status'], 'executed')

    def test_duration_validation_and_audit_failure_cannot_enable_pause(self):
        from monit_docker.audit import AuditError
        for value in (-1, 86401, 'inf', '1.5'):
            with self.assertRaises(SystemExit):
                self.change(value)
        with patch('monit_docker.audit.AuditJournal.record', side_effect=AuditError('unavailable')):
            self.assertEqual(self.change(60), 119)
        self.assertFalse(self.path.exists())

    def test_expiry_restarts_sustained_condition_timer(self):
        args = ('--trigger-after', '60', '--max-gap', '90', '--cmd-if', 'status == running ? restart')
        self.assertEqual(self.cron(100, *args)[1][0]['status'], 'pending')
        self.change(30, now=110)
        self.assertEqual(self.cron(120, *args)[1][0]['status'], 'maintenance')
        self.assertEqual(self.cron(140, *args)[1][0]['status'], 'pending')
        self.assertEqual(self.cron(200, *args)[1][0]['status'], 'executed')

    def test_service_keeps_measurements_and_manual_actions_during_maintenance(self):
        import time
        self.change(3600, now=time.time())
        obj = self.client.containers.list.return_value[0]
        obj.id = ID
        with patch('sys.argv', ['monit-docker', '-c', str(self.conf), 'serve',
                               '--state-file', str(self.path), '--rsc', 'health', '--cmd', 'restart']):
            command = cli.MonitDockerSubCmdServe(cli.argv_parse_check())
        actions = ManualActions(command._manual_action, 'https://monitor.test', 'b' * 64, audit=command.audit)
        monitor = MonitorService(command._cycle, manual_actions=actions)
        monitor.run_cycle()
        data = monitor.status()
        self.assertTrue(data['ready'])
        self.assertTrue(data['containers'][0]['maintenance_active'])
        self.assertEqual(data['actions']['maintenance'], 1)
        self.assertIn('monit_docker_container_maintenance_active{id="' + ID + '",name="demo"} 1', render_metrics(data))
        obj.restart.assert_not_called()
        actions.submit(dict(request_id='c' * 32, container_id=ID, action='restart'), data, actor='operator')
        self.assertTrue(monitor.run_pending_action())
        obj.restart.assert_called_once_with()
        self.assertEqual(monitor.status()['manual_actions']['recent'][0]['status'], 'succeeded')

    def test_controls_require_opt_in_and_keep_protection(self):
        data = dict(ready=True, containers=[dict(id=ID, name='web', status='running')])
        payload = dict(request_id='c' * 32, container_id=ID, action='maintenance-15m')
        actions = ManualActions(None, 'https://monitor.test', 'b' * 64)
        self.assertNotIn('maintenance-15m', actions.status()['allowed_states'])
        with self.assertRaises(ActionRejected):
            actions.submit(payload, data)
        actions = ManualActions(None, 'https://monitor.test', 'b' * 64, allow_maintenance=True)
        data['containers'][0]['manual_actions_protected'] = True
        with self.assertRaisesRegex(ActionRejected, 'container_protected'):
            actions.submit(payload, data)
        data['containers'][0]['manual_actions_protected'] = False
        self.assertEqual(actions.submit(payload, data, actor='operator')['status'], 'queued')


class MaintenanceStateTests(unittest.TestCase):
    def test_migration_restart_reservation_does_not_drop_maintenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            for version in (1, 2, 3):
                data = dict(version=version, cooldowns={ID: 1000})
                if version >= 2:
                    data['observations'] = {ID: [1, 2]}
                if version >= 3:
                    data['restarts'] = {ID: 1}
                path.write_text(json.dumps(data))
                with LocalState(str(path)) as state:
                    state.set_maintenance(ID, 60, 100)
                    state.reserve_restart('b' * 64, 3)
                    state.reset_restarts('b' * 64)
                    state.reserve('c' * 64, 100, 30)
                with LocalState(str(path)) as state:
                    self.assertEqual(state.version, 4)
                    self.assertEqual(state.maintenance, {ID: 160})
                    self.assertEqual(state.entries[ID], 1000)
                    self.assertEqual(state.restarts, data.get('restarts', {}))

    def test_failed_write_invalid_state_and_contention_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with LocalState(str(path)) as state:
                state.set_maintenance(ID, 60, 100)
                before = path.read_bytes()
                with self.assertRaises(MonitoringError), patch('monit_docker.adapters.state.os.rename', side_effect=OSError('full')):
                    state.set_maintenance(ID, 0, 120)
                self.assertEqual(state.maintenance, {ID: 160})
                self.assertEqual(path.read_bytes(), before)
                with self.assertRaises(MonitoringError):
                    with LocalState(str(path)):
                        pass
            for value in (True, -1, float('nan'), '160'):
                data = json.loads(before)
                data['maintenance'][ID] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(MonitoringError):
                    with LocalState(str(path)):
                        pass
