"""Regression tests using the real CLI and a simulated Docker client."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from docker.errors import APIError, DockerException
from docker.models.containers import ExecResult


from monit_docker import cli as md
from monit_docker.adapters import docker as docker_adapter


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
        with patch.object(docker_adapter, 'ContainerSnapshot', wraps=docker_adapter.ContainerSnapshot) as model:
            with patch.object(md.sys.stdout, 'write') as write:
                self.assertEqual(self.invoke('stats', '--rsc', 'mem_usage'), 0)
            self.assertEqual(model.call_args.kwargs['mem_usage'], 80)
            self.assertEqual(json.loads(write.call_args.args[0])['demo']['mem_usage'], '80.00 B')

    def test_monit_text_preserves_raw_units(self):
        with patch.object(md.sys.stdout, 'write') as write:
            self.assertEqual(self.invoke('monit', '--rsc', 'mem_usage', '--rsc', 'mem_limit'), 0)
        self.assertEqual(write.call_args.args[0], 'demo|mem_usage:80|mem_limit:100\n')

    def test_invalid_later_rule_prevents_any_earlier_action_or_connection(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.invoke('monit', '--cmd', 'restart', '--cmd', '@missing'), 110)
        obj.restart.assert_not_called()
        self.client.containers.list.assert_not_called()
        md.docker.from_env.assert_not_called()

    def test_status_exit_closes_client_and_does_not_sample(self):
        obj = self.client.containers.list.return_value[0]
        obj.status = 'exited'
        self.assertEqual(self.invoke('monit', '--rsc', 'status'), 50)
        self.client.api.close.assert_called_once_with()
        obj.stats.assert_not_called()

    def test_same_command_instance_can_run_twice(self):
        argv = ['monit-docker', '-c', str(self.conf), 'monit', '--cmd', 'restart']
        with patch.object(md.sys, 'argv', argv):
            command = md.MonitDockerSubCmdMonit(md.argv_parse_check())
        first = self.client.containers.list.return_value[0]
        command()
        second = container()
        self.client.containers.list.return_value = [second]
        command()
        first.restart.assert_called_once_with()
        second.restart.assert_called_once_with()
        self.assertEqual(self.client.api.close.call_count, 2)

    def test_pid_output_and_pidfile_keep_their_format(self):
        argv = ['monit-docker', '-c', str(self.conf), '--runtimedir', self.temp.name,
                '--logfile', str(Path(self.temp.name) / 'missing' / 'log'), 'monit', '--rsc', 'pid']
        with patch.object(md.sys, 'argv', argv), patch.object(md.sys.stdout, 'write') as write:
            self.assertEqual(md.main(md.argv_parse_check()), 0)
        self.assertEqual((Path(self.temp.name) / 'demo.pid').read_text(), '123\n')
        self.assertEqual(write.call_args.args[0], 'demo|pid:123\n')

    def test_exec_exit_codes_with_and_without_propagation(self):
        for propagate in (False, True):
            for code in (0, 1, 42, 116, 137, 255):
                with self.subTest(propagate=propagate, code=code):
                    obj = container()
                    obj.exec_run.return_value = ExecResult(code, b'')
                    self.client.containers.list.return_value = [obj]
                    flags = ['--propagate-exit-code'] if propagate else []
                    expected = code if propagate or code == 0 else 116
                    self.assertEqual(self.invoke('monit', *flags, '--cmd', '(probe)'), expected)

    def test_propagation_stops_at_first_failure_across_containers(self):
        first, second, third = container('one'), container('two'), container('three')
        second.exec_run.return_value = ExecResult(42, b'failed')
        third.exec_run.return_value = ExecResult(7, b'failed')
        self.client.containers.list.return_value = [first, second, third]
        self.assertEqual(self.invoke('monit', '--propagate-exit-code',
                                     '--cmd', '(probe)', '--cmd', 'restart'), 42)
        first.restart.assert_called_once_with()
        second.restart.assert_not_called()
        third.exec_run.assert_not_called()
        self.client.api.close.assert_called_once_with()

    def test_propagation_works_for_aliases_and_stops_later_alias_actions(self):
        self.conf.write_text('commands:\n  probe:\n    exec: ["(one)", "(two)", restart]\n')
        obj = self.client.containers.list.return_value[0]
        obj.exec_run.side_effect = [ExecResult(0, b'ok'), ExecResult(7, b'failed')]
        self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '@probe'), 7)
        self.assertEqual(obj.exec_run.call_count, 2)
        obj.restart.assert_not_called()

    def test_metric_rule_propagates_exec_failure(self):
        obj = self.client.containers.list.return_value[0]
        obj.exec_run.return_value = ExecResult(42, b'failed')
        self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd-if',
                                     'mem_percent > 60 ? (probe)'), 42)
        obj.stats.assert_called_once_with(stream=True)
        self.client.api.close.assert_called_once_with()

    def test_unmatched_rule_is_success_without_executing_a_command(self):
        obj = self.client.containers.list.return_value[0]
        self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd-if',
                                     'status == paused ? (probe)'), 0)
        obj.exec_run.assert_not_called()

    def test_propagation_does_not_change_action_api_error_codes(self):
        for command, method in [('(probe)', 'exec_run'), ('restart', 'restart')]:
            with self.subTest(command=command):
                obj = container()
                getattr(obj, method).side_effect = APIError('failed')
                self.client.containers.list.return_value = [obj]
                self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', command), 116)

    def test_propagation_does_not_change_collection_or_configuration_errors(self):
        with patch.object(self.client.containers, 'list', side_effect=APIError('failed')):
            self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '(probe)'), 180)
        with patch.object(md.docker, 'from_env', side_effect=DockerException('failed')):
            self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '(probe)'), 170)
        self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '@missing'), 110)
        self.client.containers.list.return_value = []
        self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '(probe)'), 114)

    def test_missing_or_invalid_exec_status_is_not_propagated_or_wrapped(self):
        for status in (None, -1, 256, '42', True, False, 0.0, 42.5):
            with self.subTest(status=status):
                obj = container()
                obj.exec_run.return_value = ExecResult(status, b'')
                self.client.containers.list.return_value = [obj]
                self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '(probe)'), 116)

    def test_streaming_or_detached_exec_without_status_remains_an_error(self):
        for option in ('stream', 'detach'):
            with self.subTest(option=option):
                self.conf.write_text('commands:\n  probe:\n    exec:\n'
                                     '      - "(probe)":\n          kwargs:\n            %s: true\n' % option)
                obj = container()
                obj.exec_run.return_value = ExecResult(None, None)
                self.client.containers.list.return_value = [obj]
                self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '@probe'), 116)

    def test_cleanup_failure_does_not_replace_propagated_status(self):
        obj = self.client.containers.list.return_value[0]
        obj.exec_run.return_value = ExecResult(42, b'failed')
        self.client.api.close.side_effect = RuntimeError('cleanup failed')
        with self.assertLogs('monit-docker', level='ERROR'):
            self.assertEqual(self.invoke('monit', '--propagate-exit-code', '--cmd', '(probe)'), 42)

    def test_propagation_requires_command_mode(self):
        for args in [('monit', '--propagate-exit-code'),
                     ('monit', '--propagate-exit-code', '--rsc', 'cpu_percent'),
                     ('stats', '--propagate-exit-code')]:
            with self.subTest(args=args), patch.object(md.sys.stderr, 'write'), self.assertRaises(SystemExit) as error:
                self.invoke(*args)
            self.assertEqual(error.exception.code, 2)
        self.client.containers.list.assert_not_called()


if __name__ == '__main__':
    unittest.main()
