"""Mount modes use the directory's open descriptor, never path-prefix guessing."""

import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.docker import DockerCollector
from monit_docker.adapters.filesystems import _MODE_COMMAND, _MAX_MOUNT_OUTPUT, _exec_mode, mount_mode
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
from monit_docker.cli import MonitDockerSubCmdStats
from monit_docker.core import MonitoringEngine, RuleEvaluator
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.filesystems import FilesystemSample
from monit_docker.outputs.prometheus import render_metrics
from test_monit_docker import container

GROUPS = {'data': {'paths': ['/data', '/var/log']}}
MOUNT = b'pos:\t0\nflags:\t0100000\nmnt_id:\t42\nino:\t1\n42 1 0:10 / /data ro,relatime - ext4 /dev/sda rw\n'


class FilesystemModeTests(unittest.TestCase):
    def test_exact_mount_id_with_bind_mounts_and_superblock_flags(self):
        self.assertEqual(mount_mode(MOUNT), 'ro')
        self.assertEqual(mount_mode(MOUNT.replace(b'ro,relatime', b'rw,relatime')), 'rw')
        self.assertEqual(mount_mode(MOUNT.replace(b'ro,relatime', b'rw,relatime').replace(b'/dev/sda rw', b'/dev/sda ro')), 'ro')
        # A stacked/nested mount or escaped path cannot override the actual fd's ID.
        self.assertEqual(mount_mode(MOUNT + b'43 42 0:10 / /data/space\\040name rw - ext4 /dev/sda rw\n'), 'ro')
        for value in (b'', MOUNT.replace(b'mnt_id:', b'missing:'), MOUNT.replace(b'42 1', b'43 1'),
                      MOUNT + MOUNT, MOUNT.replace(b'ro,relatime', b'ro,rw'),
                      MOUNT.replace(b'/dev/sda rw', b'/dev/sda relatime')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                mount_mode(value)

    def test_local_probe_follows_symlinks_and_passes_shell_punctuation_literally(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'space ; $(touch PWNED)'
            target.mkdir()
            link = Path(directory) / 'link'
            link.symlink_to(target)
            for path in (target, link, Path('/sys')):
                output = subprocess.check_output(list(_MODE_COMMAND) + [str(path)], cwd=directory, timeout=5)
                expected = 'ro' if os.statvfs(path).f_flag & os.ST_RDONLY else 'rw'
                self.assertEqual(mount_mode(output), expected)
            self.assertFalse((Path(directory) / 'PWNED').exists())
            missing = subprocess.run(list(_MODE_COMMAND) + [directory + '/missing'], capture_output=True, timeout=5)
            self.assertNotEqual(missing.returncode, 0)

    def test_exec_mode_is_bounded_and_closes_its_socket_on_failure(self):
        for output, raw, code in ((MOUNT, False, 0), (MOUNT, False, 1),
                                  (b'missing', False, 0),
                                  (struct.pack('>BxxxI', 1, _MAX_MOUNT_OUTPUT + 1), True, 0)):
            with self.subTest(raw=raw, code=code):
                reader, writer = socket.socketpair()
                self.addCleanup(reader.close)
                self.addCleanup(writer.close)
                writer.sendall(output if raw else struct.pack('>BxxxI', 1, len(output)) + output)
                writer.shutdown(socket.SHUT_WR)
                api = Mock()
                api.exec_create.return_value = {'Id': 'mode'}
                api.exec_start.return_value = reader
                api.exec_inspect.return_value = {'Running': False, 'ExitCode': code}
                if output == MOUNT and code == 0:
                    self.assertEqual(_exec_mode(api, 'id', '/data'), 'ro')
                else:
                    with self.assertRaises(ValueError):
                        _exec_mode(api, 'id', '/data')
                self.assertEqual(reader.fileno(), -1)
                self.assertEqual(api.exec_create.call_args.args[1], list(_MODE_COMMAND) + ['/data'])

    def test_mode_conditions_are_validated_offline_and_share_same_path_semantics(self):
        parser = RuleParser(dir_groups=GROUPS, conditions={'full_ro': {'expr': [
            'fs_mode[data] == ro', 'disk_percent[data] > 90']}})
        rule = parser.parse('@full_ro ? (true)')
        samples = [FilesystemSample('data', path, 0, 0, 0, disk, 0, 0, 0, 0, mode)
                   for path, disk, mode in [('/data', 10, 'ro'), ('/var/log', 95, 'rw')]]
        self.assertFalse(RuleEvaluator().matches(rule, ContainerSnapshot(filesystems=samples)))
        samples[1] = samples[1]._replace(fs_mode='ro')
        self.assertTrue(RuleEvaluator().matches(rule, ContainerSnapshot(filesystems=samples)))
        for expression in ('fs_mode[data] > ro', 'fs_mode[data] == unknown', 'fs_mode[data] == 1',
                           '1 < fs_mode[data] == rw', 'fs_mode[data] in (ro,)', 'fs_mode[data] in rw'):
            with self.subTest(expression=expression), self.assertRaises(RuleSyntaxError):
                parser.parse(expression + ' ? (true)')
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yml'
            config.write_text('dir-groups:\n  data:\n    paths: [/data]\n')
            check_configuration(str(config), expressions=['fs_mode[data] in (ro,rw) ? (true)'])
            with self.assertRaises(ConfigurationCheckError):
                check_configuration(str(config), expressions=['fs_mode[data] == nope ? (true)'])

    def test_mode_only_collects_once_per_path_without_statistics(self):
        obj, client, executor = container(), Mock(), Mock()
        client.containers.list.return_value = [obj]
        groups = dict(GROUPS, shared={'paths': ['/data']})
        engine = MonitoringEngine(DockerCollector(lambda: client, dir_groups=groups), executor)
        rule = RuleParser(dir_groups=groups).parse('fs_mode[data] == ro ? restart')
        with patch('monit_docker.adapters.filesystems._exec_mode', side_effect=['ro', 'rw']) as mode, patch(
                'monit_docker.adapters.filesystems._exec_stat') as stat:
            result = engine.run_once(rules=(rule,), resources=('fs_mode[shared]',))
        self.assertEqual(mode.call_count, 2)
        stat.assert_not_called()
        obj.stats.assert_not_called()
        executor.execute.assert_called_once()
        sample = result.snapshots[0]
        self.assertEqual([s.fs_mode for s in sample.filesystems], ['ro', 'rw', 'ro'])
        self.assertTrue(all(s.disk_percent is None for s in sample.filesystems))
        self.assertEqual(ContainerSnapshot(**sample.to_dict()).filesystems, sample.filesystems)
        command = object.__new__(MonitDockerSubCmdStats)
        command.options = Mock(output='json', resource=['fs_mode[data]'])
        with patch('sys.stdout', new_callable=io.StringIO) as output:
            command._output_snapshot(sample)
        self.assertEqual(json.loads(output.getvalue())['demo']['fs_mode[data]'], {'/data': 'ro', '/var/log': 'rw'})
        data = dict(ready=True, running=False, cycles_total=1, errors_total=0,
                    last_success_at=None, actions={}, containers=[sample.to_dict()])
        self.assertIn('monit_docker_container_filesystem_read_only{id="demo",name="demo",group="data",path="/data"} 1', render_metrics(data))
        self.assertIn('path="/var/log"} 0', render_metrics(data))
        data['ready'] = False
        self.assertNotIn('path="/data"', render_metrics(data))

    def test_unavailable_mode_aborts_remediation(self):
        obj, client, executor = container(), Mock(), Mock()
        client.containers.list.return_value = [obj]
        engine = MonitoringEngine(DockerCollector(lambda: client, dir_groups=GROUPS), executor)
        rule = RuleParser(dir_groups=GROUPS).parse('fs_mode[data] != rw ? restart')
        with patch('monit_docker.adapters.filesystems._exec_mode', side_effect=ValueError('not available')):
            with self.assertRaises(MonitoringError) as error:
                engine.run_once(rules=(rule,))
        self.assertEqual(error.exception.code, 115)
        executor.execute.assert_not_called()

    def test_overlapping_numeric_and_mode_groups_share_probes(self):
        obj, client = container(), Mock()
        client.containers.list.return_value = [obj]
        groups = dict(GROUPS, shared={'paths': ['/data']})
        engine = MonitoringEngine(DockerCollector(lambda: client, dir_groups=groups), Mock())
        with patch('monit_docker.adapters.filesystems._exec_mode', return_value='ro') as mode, patch(
                'monit_docker.adapters.filesystems._exec_stat', return_value=(1,) * 8) as stat:
            result = engine.run_once(resources=('disk_percent[data]', 'fs_mode[shared]'))
        self.assertEqual(stat.call_count, 2)
        mode.assert_called_once_with(client.api, 'demo', '/data')
        self.assertEqual([s.fs_mode for s in result.snapshots[0].filesystems], ['ro', None, 'ro'])


if __name__ == '__main__':
    unittest.main()
