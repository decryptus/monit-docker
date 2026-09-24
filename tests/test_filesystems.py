"""Filesystem checks: real framing, failure handling and rule integration."""

import io
import json
import os
import socket
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.filesystems import _exec_stat, directory_groups, stat_values
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
from monit_docker.cli import MonitDockerSubCmdStats, _resource_argument
from monit_docker.core import MonitoringEngine
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.filesystems import FilesystemSample
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.outputs.prometheus import render_metrics
from test_monit_docker import container

GROUPS = {'data': {'paths': ['/data', '/var/log']}}
STAT = b'4096 1000 100 50 200 10\n'


class FilesystemTests(unittest.TestCase):
    def make_api(self, payload=STAT, code=0, keep_open=False, raw=False):
        reader, writer = socket.socketpair()
        self.addCleanup(reader.close)
        self.addCleanup(writer.close)
        writer.sendall(payload if raw else struct.pack('>BxxxI', 1, len(payload)) + payload)
        if not keep_open:
            writer.shutdown(socket.SHUT_WR)
        api = Mock()
        api.exec_create.return_value = {'Id': 'exec-id'}
        api.exec_start.return_value = reader
        api.exec_inspect.return_value = {'Running': False, 'ExitCode': code}
        return api, reader

    def sample(self, path, disk=95, inodes=97, group='data'):
        return FilesystemSample(group, path, 900, 50, 1000, disk, 190, 10, 200, inodes)

    def test_stat_matches_local_statvfs_without_scanning_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            output = subprocess.check_output(['stat', '-f', '-c', '%S %b %f %a %c %d', '--', directory])
            actual = stat_values(output)
            expected = os.statvfs(directory)
            self.assertEqual(actual[2], expected.f_blocks * expected.f_frsize)
            self.assertEqual(actual[6], expected.f_files or None)

    def test_reserved_blocks_and_unavailable_inode_accounting(self):
        values = stat_values(STAT)
        self.assertEqual(values[:4], (900 * 4096, 50 * 4096, 1000 * 4096, 94.74))
        self.assertEqual(values[4:], (190, 10, 200, 95.0))
        self.assertEqual(stat_values(b'4096 100 90 90 0 0')[4:], (None,) * 4)
        for output in (b'bad', b'0 1 1 1 1 1', b'4096 100 90 95 1 1', b'4096 1 1 1 2 3'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                stat_values(output)

    def test_exec_uses_literal_argv_and_closes_socket(self):
        api, reader = self.make_api()
        path = '/data/space ; $(touch nope)'
        self.assertEqual(_exec_stat(api, 'container-id', path), stat_values(STAT))
        self.assertEqual(api.exec_create.call_args.args[1],
                         ['stat', '-f', '-c', '%S %b %f %a %c %d', '--', path])
        self.assertEqual(reader.fileno(), -1)

    def test_failed_exec_truncated_output_and_size_limit_are_errors(self):
        for payload, code, raw in ((STAT, 1, False), (b'invalid', 0, False),
                                   (struct.pack('>BxxxI', 1, 9000), 0, True),
                                   (struct.pack('>BxxxI', 1, 10) + b'12', 0, True)):
            with self.subTest(payload=payload):
                api, reader = self.make_api(payload, code, raw=raw)
                with self.assertRaises(ValueError):
                    _exec_stat(api, 'id', '/data')
                self.assertEqual(reader.fileno(), -1)

    def test_read_timeout_is_bounded_and_closes_socket(self):
        api, reader = self.make_api(keep_open=True)
        with patch('monit_docker.adapters.filesystems._EXEC_TIMEOUT', 0.02):
            with self.assertRaises(socket.timeout):
                _exec_stat(api, 'id', '/data')
        self.assertEqual(reader.fileno(), -1)

    def test_groups_support_imports_templates_and_offline_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'paths.yml').write_text('data:\n  paths: ["${vars[\'path\']}", /var/log]\n')
            config = root / 'config.yml'
            config.write_text('vars: {path: /data}\ndir-groups:\n  "@import_dir-group": paths.yml\n'
                              'conditions:\n  full:\n    expr: ["disk_percent[data] > 90"]\n')
            loaded = Configuration(str(config)).load()
            self.assertEqual(loaded['dir-groups'], GROUPS)
            check_configuration(str(config), expressions=['@full ? (true)'])
            with self.assertRaises(ConfigurationCheckError):
                check_configuration(str(config), expressions=['disk_percent[missing] > 90 ? (true)'])
        for paths in ([], ['/data', 'relative'], ['/bad\npath'], '/data', [3]):
            with self.subTest(paths=paths), self.assertRaises(MonitoringError):
                directory_groups({'data': {'paths': paths}})

    def test_group_conditions_must_match_the_same_path(self):
        parser = RuleParser(conditions={'full': {'expr': ['disk_percent[data] > 90',
                                                         'inode_percent[data] > 95']}}, dir_groups=GROUPS)
        rule = parser.parse('@full ? (true)')
        snapshot = ContainerSnapshot(filesystems=(self.sample('/data', 95, 5), self.sample('/var/log', 5, 97)))
        self.assertFalse(RuleEvaluator().matches(rule, snapshot))
        snapshot = ContainerSnapshot(filesystems=(self.sample('/data'), self.sample('/var/log', 5, 97)))
        self.assertTrue(RuleEvaluator().matches(rule, snapshot))
        with self.assertRaises(MonitoringError):
            RuleEvaluator().matches(rule, ContainerSnapshot(filesystems=(self.sample('/data', inodes=None),)))

    def test_engine_collects_each_path_once_and_executes_one_action(self):
        obj = container()
        client = Mock()
        client.containers.list.return_value = [obj]
        groups = dict(GROUPS, other={'paths': ['/data']})
        collector = DockerCollector(lambda: client, dir_groups=groups)
        engine = MonitoringEngine(collector, DockerActionExecutor(collector))
        rule = RuleParser(dir_groups=groups).parse('disk_percent[data] > 90 ? restart')
        with patch('monit_docker.adapters.filesystems._exec_stat', return_value=stat_values(STAT)) as probe:
            result = engine.run_once(rules=(rule,), resources=('inode_percent[data]', 'disk_percent[other]'))
        self.assertEqual(probe.call_count, 2)
        obj.stats.assert_not_called()
        obj.restart.assert_called_once_with()
        self.assertEqual(len(result.actions), 1)
        self.assertEqual(len(result.snapshots[0].filesystems), 3)
        client.api.close.assert_called_once_with()

    def test_missing_path_aborts_actions_and_reports_context(self):
        obj = container()
        client = Mock()
        client.containers.list.return_value = [obj]
        collector = DockerCollector(lambda: client, dir_groups=GROUPS)
        engine = MonitoringEngine(collector, DockerActionExecutor(collector))
        rule = RuleParser(dir_groups=GROUPS).parse('disk_percent[data] > 90 ? restart')
        with patch('monit_docker.adapters.filesystems._exec_stat', side_effect=[stat_values(STAT), ValueError()]):
            with self.assertRaisesRegex(MonitoringError, '/var/log'):
                engine.run_once(rules=(rule,))
        obj.restart.assert_not_called()
        client.api.close.assert_called_once_with()

    def test_cpu_only_never_executes_a_filesystem_probe(self):
        obj, client = container(), Mock()
        client.containers.list.return_value = [obj]
        collector = DockerCollector(lambda: client, dir_groups=GROUPS)
        MonitoringEngine(collector, Mock()).run_once(resources=('cpu_percent',))
        client.api.exec_create.assert_not_called()

    def test_json_and_prometheus_preserve_group_and_path(self):
        snapshot = ContainerSnapshot(id='id', name='web', status='running',
                                     filesystems=(self.sample('/data'),))
        self.assertEqual(ContainerSnapshot(**snapshot.to_dict()).filesystems, snapshot.filesystems)
        options = Mock(output='json', resource=['disk_percent[data]', 'disk_available[data]'])
        command = object.__new__(MonitDockerSubCmdStats)
        command.options = options
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            command._output_snapshot(snapshot)
        values = json.loads(output.getvalue())['web']
        self.assertEqual(values['disk_percent[data]'], {'/data': 95})
        self.assertEqual(values['disk_available[data]'], {'/data': '50.00 B'})
        data = dict(ready=True, running=False, cycles_total=1, errors_total=0,
                    last_success_at=None, actions={}, containers=[snapshot.to_dict()])
        rendered = render_metrics(data)
        self.assertIn('monit_docker_container_disk_usage_percent{id="id",name="web",group="data",path="/data"} 95', rendered)
        data['ready'] = False
        self.assertNotIn('path="/data"', render_metrics(data))
        self.assertEqual(_resource_argument('inode_available[data]'), 'inode_available[data]')


if __name__ == '__main__':
    unittest.main()
