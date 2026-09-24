"""Persistent observed-condition durations: real files, processes and CLI paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from docker.errors import APIError
import monit_docker
from monit_docker import cli
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.state import LocalState
from monit_docker.core.policy import TriggerPolicy
from monit_docker.domain.errors import MonitoringError
from monit_docker.outputs.prometheus import render_metrics
import test_monit_docker as legacy


class TriggerStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'state.json'
        self.rule = RuleParser().parse('status == running ? restart')

    def observe(self, now, matched=True, after=120, gap=60, dry_run=False, rule=None, ident='a'):
        rule = rule or self.rule
        with LocalState(str(self.path)) as state:
            policy = TriggerPolicy(state, [rule], 300, after, gap,
                                   clock=lambda: now, read_only=dry_run)
            ready = policy.observe(ident, rule, matched, read_only=dry_run)
            return ready

    def test_exact_boundary_and_state_survives_a_fresh_process(self):
        self.assertFalse(self.observe(100))
        self.assertFalse(self.observe(160))
        code = ('from monit_docker.adapters.state import LocalState\n'
                'from monit_docker.adapters.rules import RuleParser\n'
                'from monit_docker.core.policy import TriggerPolicy\n'
                'import sys\nr = RuleParser().parse("status == running ? restart")\n'
                'with LocalState(sys.argv[1]) as s:\n'
                ' p = TriggerPolicy(s, [r], 300, 120, 60, clock=lambda: 220)\n'
                ' assert p.observe("a", r, True)\n')
        env = dict(os.environ, PYTHONPATH=str(Path(monit_docker.__file__).resolve().parent.parent))
        result = subprocess.run([sys.executable, '-c', code, str(self.path)], env=env,
                                cwd=str(self.path.parent), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['cooldowns'], {})
        self.assertEqual(list(data['observations'].values()), [[100, 220]])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_false_gap_and_clock_reversal_reset_the_streak(self):
        for middle, matched, next_time in [(160, False, 220), (161, True, 221), (99, True, 159)]:
            with self.subTest(middle=middle, matched=matched):
                self.path.unlink(missing_ok=True)
                self.assertFalse(self.observe(100))
                self.assertFalse(self.observe(middle, matched))
                self.assertFalse(self.observe(next_time))
                if matched:
                    self.assertTrue(self.observe(next_time + 60))
                else:
                    self.assertFalse(self.observe(next_time + 60))
                    self.assertTrue(self.observe(next_time + 120))

    def test_changed_container_rule_or_timing_starts_a_new_streak(self):
        changes = [dict(ident='replacement'), dict(after=180), dict(gap=61),
                   dict(rule=RuleParser().parse('status == paused ? restart'))]
        for change in changes:
            with self.subTest(change=change):
                self.path.unlink(missing_ok=True)
                self.observe(100)
                self.observe(160)
                self.assertFalse(self.observe(220, **change))
                self.assertEqual(len(json.loads(self.path.read_text())['observations']), 1)

    def test_preview_preserves_existing_bytes_and_creates_no_state(self):
        self.assertFalse(self.observe(100, dry_run=True))
        self.assertFalse(self.path.exists())
        self.observe(100)
        self.observe(160)
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns
        self.assertTrue(self.observe(220, dry_run=True))
        self.observe(220, matched=False, dry_run=True)
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mtime_ns), before)

    def test_v1_cooldowns_survive_upgrade_and_disabling_the_delay(self):
        key = 'b' * 64
        with LocalState(str(self.path)) as state:
            state.reserve(key, 100, 300)
        self.assertEqual(json.loads(self.path.read_text())['version'], 1)
        self.observe(100)
        with LocalState(str(self.path)) as state:
            self.assertFalse(state.reserve(key, 150, 1))
            TriggerPolicy(state, [self.rule], 300)
        data = json.loads(self.path.read_text())
        self.assertEqual(data['version'], 2)
        self.assertEqual(data['cooldowns'][key], 400)
        self.assertEqual(data['observations'], {})
        self.assertFalse(self.observe(160))

    def test_crash_after_invalidating_old_observations_cannot_resume_them(self):
        self.observe(100)
        self.observe(160)
        env = dict(os.environ, PYTHONPATH=str(Path(monit_docker.__file__).resolve().parent.parent))
        result = subprocess.run([sys.executable, '-c',
            'import os,sys\nfrom monit_docker.adapters.state import LocalState\n'
            'from monit_docker.core.policy import TriggerPolicy\n'
            'with LocalState(sys.argv[1]) as s:\n'
            ' TriggerPolicy(s, [], 300, 120, 60)\n os._exit(0)\n', str(self.path)],
            env=env, cwd=str(self.path.parent), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.observe(220))

    def test_invalid_v2_state_is_rejected_without_reset(self):
        for observations in ([], {'bad': [1, 2]}, {'a' * 64: [2, 1]},
                             {'a' * 64: [True, 2]}, {'a' * 64: [1, float('nan')]},
                             {'a' * 64: [1]}, {'a' * 64: [-1, 2]}):
            with self.subTest(observations=observations):
                self.path.write_text(json.dumps(dict(version=2, cooldowns={}, observations=observations)))
                before = self.path.read_bytes()
                with self.assertRaises(MonitoringError) as error:
                    with LocalState(str(self.path)):
                        self.fail('invalid state accepted')
                self.assertEqual(error.exception.code, 118)
                self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_replacement_preserves_disk_and_memory(self):
        key = 'a' * 64
        invalid = [[], {'bad': [1, 2]}, {1: [1, 2]}, {key: [2, 1]},
                   {key: [True, 2]}, {key: [1, float('nan')]},
                   {key: [1, float('inf')]}, {key: [1]}, {key: [-1, 2]},
                   {key: (1, 2)}, {key: [1, 10 ** 400]}]
        for version in (1, 2):
            self.path.unlink(missing_ok=True)
            with LocalState(str(self.path)) as state:
                state.reserve('b' * 64, 100, 300)
                if version == 2:
                    state.replace_observations({key: [1, 2]})
                before = self.path.read_bytes(), self.path.stat().st_mtime_ns
                expected = dict(state.observations)
                for read_only in (False, True):
                    for observations in invalid:
                        with self.subTest(version=version, read_only=read_only,
                                          observations=observations):
                            with self.assertRaises(ValueError):
                                state.replace_observations(observations, read_only=read_only)
                            self.assertEqual(state.observations, expected)
                            self.assertEqual(state.version, version)
                            self.assertEqual(state.entries, {'b' * 64: 400})
                            self.assertEqual((self.path.read_bytes(),
                                              self.path.stat().st_mtime_ns), before)

    def test_invalid_replacement_does_not_create_state(self):
        with LocalState(str(self.path)) as state:
            for read_only in (False, True):
                with self.assertRaises(ValueError):
                    state.replace_observations({'a' * 64: [2, 1]}, read_only=read_only)
                self.assertEqual(state.observations, {})
                self.assertEqual(state.version, 1)
                self.assertFalse(self.path.exists())

    def test_replacement_does_not_retain_caller_owned_lists(self):
        key = 'a' * 64
        for read_only in (False, True):
            self.path.unlink(missing_ok=True)
            with LocalState(str(self.path)) as state:
                observations = {key: [1, 2]}
                state.replace_observations(observations, read_only=read_only)
                observations[key][0] = 3
                self.assertEqual(state.observations, {key: [1, 2]})
                state.reserve('b' * 64, 100, 300)
            with LocalState(str(self.path)) as state:
                self.assertEqual(state.observations, {key: [1, 2]})


class TriggerCliTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    def cycle(self, now, *args, rule='status == running ? restart'):
        self.client.containers.list.return_value[0].stats.return_value = legacy.container().stats.return_value
        with patch('monit_docker.core.policy.time.time', return_value=now), patch.object(cli.sys.stdout, 'write') as write:
            result = self.invoke('cron', '--state-file', str(Path(self.temp.name) / 'job.json'),
                                 '--trigger-after', '120', '--max-gap', '60', '--cmd-if', rule, *args)
        return result, [json.loads(c.args[0])['status'] for c in write.call_args_list]

    def test_pending_then_action_then_cooldown_on_continuing_condition(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.cycle(100), (0, ['pending']))
        self.assertEqual(self.cycle(160), (0, ['pending']))
        self.assertEqual(self.cycle(220), (0, ['executed']))
        for t in range(280, 520, 60):
            self.assertEqual(self.cycle(t), (0, ['cooldown']))
        self.assertEqual(self.cycle(520), (0, ['executed']))
        self.assertEqual(obj.restart.call_count, 2)

    def test_recovery_then_new_streak(self):
        obj = self.client.containers.list.return_value[0]
        self.cycle(100)
        obj.status = 'paused'
        self.assertEqual(self.cycle(160), (0, []))
        obj.status = 'running'
        self.assertEqual(self.cycle(220), (0, ['pending']))
        self.assertEqual(self.cycle(280), (0, ['pending']))
        self.assertEqual(self.cycle(340), (0, ['executed']))

    def test_failed_or_unavailable_metric_clears_its_previous_observation(self):
        obj = self.client.containers.list.return_value[0]
        rule = 'mem_percent > 60 ? restart'
        for failure in ('api', 'stopped'):
            with self.subTest(failure=failure):
                (Path(self.temp.name) / 'job.json').unlink(missing_ok=True)
                self.assertEqual(self.cycle(100, rule=rule), (0, ['pending']))
                if failure == 'api':
                    obj.stats.side_effect = APIError('unavailable')
                    self.assertNotEqual(self.cycle(160, rule=rule)[0], 0)
                    obj.stats.side_effect = None
                else:
                    obj.status = 'exited'
                    self.assertEqual(self.cycle(160, rule=rule), (0, []))
                    obj.status = 'running'
                self.assertEqual(self.cycle(220, rule=rule), (0, ['pending']))
                self.assertEqual(self.cycle(280, rule=rule), (0, ['pending']))
                self.assertEqual(self.cycle(340, rule=rule), (0, ['executed']))

    def test_state_write_failure_prevents_pending_rule_action(self):
        obj = self.client.containers.list.return_value[0]
        self.cycle(100)
        self.cycle(160)
        with patch('monit_docker.adapters.state.os.rename', side_effect=OSError('disk full')):
            self.assertEqual(self.cycle(220)[0], 118)
        obj.restart.assert_not_called()

    def test_failed_observation_write_after_invalidation_cannot_trigger(self):
        obj = self.client.containers.list.return_value[0]
        self.cycle(100)
        self.cycle(160)
        rename = os.rename
        calls = []
        def fail_second(*args):
            calls.append(args)
            if len(calls) == 2:
                raise OSError('disk full')
            return rename(*args)
        with patch('monit_docker.adapters.state.os.rename', side_effect=fail_second):
            self.assertEqual(self.cycle(220)[0], 118)
        obj.restart.assert_not_called()
        self.assertEqual(self.cycle(280), (0, ['pending']))

    def test_unselected_container_is_pruned_and_cannot_resume_old_streak(self):
        obj = self.client.containers.list.return_value[0]
        self.cycle(100)
        self.cycle(160)
        self.client.containers.list.return_value = []
        with patch('monit_docker.core.policy.time.time', return_value=200):
            self.assertEqual(self.invoke('cron', '--state-file', str(Path(self.temp.name) / 'job.json'),
                                        '--trigger-after', '120', '--max-gap', '60',
                                        '--cmd-if', 'status == running ? restart'), 114)
        self.client.containers.list.return_value = [obj]
        self.assertEqual(self.cycle(220), (0, ['pending']))

    def test_failed_action_retains_cooldown_after_delay_has_elapsed(self):
        obj = self.client.containers.list.return_value[0]
        self.cycle(100)
        self.cycle(160)
        obj.restart.side_effect = APIError('failed')
        self.assertEqual(self.cycle(220)[0], 116)
        self.assertEqual(self.cycle(280), (0, ['cooldown']))
        obj.restart.assert_called_once_with()

    def test_unconditional_commands_keep_their_immediate_behavior(self):
        self.assertEqual(self.cycle(100, rule='restart'), (0, ['executed']))
        data = json.loads((Path(self.temp.name) / 'job.json').read_text())
        self.assertEqual(data['version'], 3)
        self.assertEqual(data['observations'], {})
        self.assertEqual(list(data['restarts'].values()), [1])

    def test_serve_and_cron_share_observations_and_expose_pending_counter(self):
        self.cycle(100)
        path = Path(self.temp.name) / 'job.json'
        def run(monitor, *args):
            with patch('monit_docker.core.policy.time.time', return_value=160):
                monitor.run_cycle()
            self.assertEqual(monitor.status()['actions']['pending'], 1)
            self.assertIn('monit_docker_action_decisions_total{outcome="pending"} 1',
                          render_metrics(monitor.status()))
            with patch('monit_docker.core.policy.time.time', return_value=220):
                monitor.run_cycle()
            self.assertEqual(monitor.status()['actions']['executed'], 1)
            self.assertTrue(monitor.status()['ready'])
        with patch('monit_docker.adapters.http.run_server', side_effect=run):
            self.assertEqual(self.invoke('serve', '--rsc', 'status', '--state-file', str(path),
                                        '--trigger-after', '120', '--max-gap', '60',
                                        '--cmd-if', 'status == running ? restart'), 0)
        self.client.containers.list.return_value[0].restart.assert_called_once_with()

    def test_serve_without_rules_does_not_change_existing_trigger_state(self):
        self.cycle(100)
        path = Path(self.temp.name) / 'job.json'
        before = path.read_bytes(), path.stat().st_mtime_ns
        with patch('monit_docker.adapters.http.run_server', side_effect=lambda monitor, *args: monitor.run_cycle()):
            self.assertEqual(self.invoke('serve', '--rsc', 'status', '--state-file', str(path)), 0)
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_invalid_trigger_options_fail_before_docker(self):
        cases = [('--trigger-after', '-1'), ('--trigger-after', 'nan'),
                 ('--trigger-after', 'inf'), ('--trigger-after', '60'),
                 ('--trigger-after', '60', '--max-gap', '0'),
                 ('--trigger-after', '60', '--max-gap', 'nan'),
                 ('--trigger-after', '60', '--max-gap', 'inf'), ('--max-gap', '60')]
        for mode in ('cron', 'serve'):
            for args in cases:
                with self.subTest(mode=mode, args=args), self.assertRaises(SystemExit) as error:
                    self.invoke(mode, '--state-file', str(Path(self.temp.name) / 'job.json'),
                                '--cmd', 'restart', *args)
                self.assertEqual(error.exception.code, 2)
        self.client.containers.list.assert_not_called()


if __name__ == '__main__':
    unittest.main()
