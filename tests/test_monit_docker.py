"""Regression tests using the real CLI and a simulated Docker client."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from docker.errors import APIError
from docker.models.containers import ExecResult


from monit_docker import cli as md


def container(name='demo', cpu=256):
    obj = Mock()
    obj.name, obj.id, obj.status = name, name, 'running'
    obj.attrs = {'State': {'Pid': 123}}
    samples = [
        {'read': str(i), 'cpu_stats': {
            'system_cpu_usage': 1000 + i * 100,
            'online_cpus': 4,
            'cpu_usage': {'total_usage': 100 + i * cpu / 4}},
         'memory_stats': {'usage': 80, 'limit': 100}}
        for i in range(2)
    ]
    obj.stats.return_value = iter(json.dumps(s).encode() for s in samples)
    obj.exec_run.return_value = ExecResult(0, b'ok')
    return obj


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conf = Path(self.temp.name) / 'config.yml'
        self.client = Mock()
        self.client.containers.list.return_value = [container()]
        self.addCleanup(patch.stopall)
        patch.object(md, 'MONIT_DOCKER_CONFIG', None).start()
        patch.object(md.docker, 'from_env', return_value=self.client).start()

    def invoke(self, *args):
        argv = ['monit-docker', '-c', str(self.conf), '--runtimedir', '',
                '--logfile', str(Path(self.temp.name) / 'missing' / 'log')]
        with patch.object(md.sys, 'argv', argv + list(args)):
            return md.main(md.argv_parse_check())

    def test_unknown_group_without_configuration_never_lists_containers(self):
        self.assertEqual(self.invoke('--ctn-group', 'missing', 'monit',
                                     '--cmd', 'restart'), 110)
        self.client.containers.list.assert_not_called()

    def test_unknown_group_with_other_groups_never_lists_containers(self):
        self.conf.write_text('ctn-groups:\n  web:\n    match: ["name:web*"]\n')
        self.assertEqual(self.invoke('--ctn-group', 'missing', 'monit',
                                     '--cmd', 'restart'), 110)
        self.client.containers.list.assert_not_called()

    def test_known_group_only_targets_matching_container(self):
        self.conf.write_text('ctn-groups:\n  web:\n    match: ["name:web*"]\n')
        web, db = container('web'), container('db')
        self.client.containers.list.return_value = [web, db]
        self.assertEqual(self.invoke('--ctn-group', 'web', 'monit', '--cmd', 'restart'), 0)
        web.restart.assert_called_once_with()
        db.restart.assert_not_called()

    def test_mixed_rules_execute_once_for_each_container(self):
        objects = [container('one'), container('two')]
        self.client.containers.list.return_value = objects
        self.assertEqual(self.invoke('monit', '--cmd', 'restart',
                                     '--cmd-if', 'mem_percent > 60 ? pause'), 0)
        for obj in objects:
            obj.restart.assert_called_once_with()
            obj.pause.assert_called_once_with()

    def test_nonzero_exec_stops_later_actions_and_containers(self):
        first, second = container('one'), container('two')
        first.exec_run.return_value = ExecResult(42, b'failed')
        self.client.containers.list.return_value = [first, second]
        self.assertEqual(self.invoke('monit', '--cmd', '(false)', '--cmd', 'restart'), 116)
        first.restart.assert_not_called()
        second.exec_run.assert_not_called()

    def test_docker_exec_api_error_is_not_swallowed(self):
        self.client.containers.list.return_value[0].exec_run.side_effect = APIError('failed')
        self.assertEqual(self.invoke('monit', '--cmd', '(false)'), 116)

    def test_docker_action_api_error_is_reported(self):
        self.client.containers.list.return_value[0].restart.side_effect = APIError('failed')
        self.assertEqual(self.invoke('monit', '--cmd', 'restart'), 116)

    def test_exec_alias_passes_options_to_docker(self):
        self.conf.write_text('commands:\n  probe:\n    exec:\n'
                             '      - "(id)":\n          kwargs:\n            user: nobody\n')
        self.assertEqual(self.invoke('monit', '--cmd', '@probe'), 0)
        self.client.containers.list.return_value[0].exec_run.assert_called_once_with('id', user='nobody')

    def test_cpu_exit_code_saturates_instead_of_wrapping(self):
        for cpu, expected in [(0, 0), (70, 70), (100, 100), (180, 100), (256, 100), (400, 100)]:
            with self.subTest(cpu=cpu):
                self.client.containers.list.return_value = [container(cpu=cpu)]
                self.assertEqual(self.invoke('monit', '--rsc', 'cpu_percent'), expected)

    def test_cpu_rule_retains_raw_multicore_percentage(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.invoke('monit', '--cmd-if', 'cpu_percent > 200 ? restart'), 0)
        obj.restart.assert_called_once_with()

    def test_cpu_stats_retain_raw_multicore_percentage(self):
        with patch.object(md.sys.stdout, 'write') as write:
            self.assertEqual(self.invoke('stats', '--rsc', 'cpu_percent'), 0)
        self.assertEqual(json.loads(write.call_args[0][0])['demo']['cpu_percent'], 256)

    def test_paused_container_is_not_restarted_by_corrected_rule(self):
        obj = self.client.containers.list.return_value[0]
        obj.status = 'paused'
        self.assertEqual(self.invoke('monit', '--cmd-if',
                                     'status not in (paused,running) ? restart'), 0)
        obj.restart.assert_not_called()
        obj.stats.assert_not_called()

    def test_group_from_previous_invocation_is_not_reused(self):
        self.conf.write_text('ctn-groups:\n  web:\n    match: ["name:demo"]\n')
        self.assertEqual(self.invoke('--ctn-group', 'web', 'monit', '--cmd', 'restart'), 0)
        self.conf.write_text('{}\n')
        self.client.reset_mock()
        self.assertEqual(self.invoke('--ctn-group', 'web', 'monit', '--cmd', 'restart'), 110)
        self.client.containers.list.assert_not_called()

    def test_command_alias_cache_is_isolated_between_invocations(self):
        obj = self.client.containers.list.return_value[0]
        self.conf.write_text('commands:\n  act:\n    exec: [restart]\n')
        self.assertEqual(self.invoke('monit', '--cmd', '@act'), 0)
        self.conf.write_text('commands:\n  act:\n    exec: [pause]\n')
        self.assertEqual(self.invoke('monit', '--cmd', '@act'), 0)
        obj.restart.assert_called_once_with()
        obj.pause.assert_called_once_with()
        self.conf.write_text('{}\n')
        self.assertEqual(self.invoke('monit', '--cmd', '@act'), 110)

    def test_condition_alias_is_isolated_between_invocations(self):
        obj = self.client.containers.list.return_value[0]
        self.conf.write_text('conditions:\n  ready:\n    expr: ["status == running"]\n')
        self.assertEqual(self.invoke('monit', '--cmd-if', '@ready ? restart'), 0)
        self.conf.write_text('conditions:\n  ready:\n    expr: ["status == paused"]\n')
        self.assertEqual(self.invoke('monit', '--cmd-if', '@ready ? restart'), 0)
        obj.restart.assert_called_once_with()
        self.conf.write_text('{}\n')
        self.assertEqual(self.invoke('monit', '--cmd-if', '@ready ? restart'), 110)

    def test_json_uses_validated_raw_snapshot_then_formats(self):
        with patch.object(md, 'ContainerSnapshot', wraps=md.ContainerSnapshot) as model:
            with patch.object(md.sys.stdout, 'write') as write:
                self.assertEqual(self.invoke('stats', '--rsc', 'mem_usage'), 0)
            self.assertEqual(model.call_args.kwargs['mem_usage'], 80)
            self.assertEqual(json.loads(write.call_args.args[0])['demo']['mem_usage'], '80.00 B')

    def test_monit_text_preserves_raw_units(self):
        with patch.object(md.sys.stdout, 'write') as write:
            self.assertEqual(self.invoke('monit', '--rsc', 'mem_usage', '--rsc', 'mem_limit'), 0)
        self.assertEqual(write.call_args.args[0], 'demo|mem_usage:80|mem_limit:100\n')


if __name__ == '__main__':
    unittest.main()
