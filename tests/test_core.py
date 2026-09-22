"""Tests for interface-independent domain and metric contracts."""

import unittest
import subprocess
import sys
from pathlib import Path
import monit_docker

from monit_docker.core import ResourceCalculator
from monit_docker.domain import ContainerSnapshot
from monit_docker.outputs.formatting import format_resource


class ResourceCalculatorTests(unittest.TestCase):
    def test_raw_values_use_base_units(self):
        calculator = ResourceCalculator()
        memory = {'usage': 1024, 'limit': 4096, 'stats': {'total_cache': 256}}
        self.assertEqual(calculator.memory_usage(memory), 768)
        self.assertEqual(calculator.memory_limit(memory), 4096)
        self.assertEqual(calculator.memory_percent(memory), 18.75)

    def test_human_values_are_only_an_output_concern(self):
        raw = ResourceCalculator().memory_usage({'usage': 1024, 'limit': 4096})
        self.assertEqual(raw, 1024)
        self.assertEqual(format_resource('mem_usage', raw), '1.00 KiB')
        self.assertIsNone(format_resource('mem_usage', None))
        self.assertEqual(format_resource('cpu_percent', 256), 256)

    def test_cpu_first_sample_is_unavailable_as_before(self):
        self.assertEqual(ResourceCalculator().get('cpu_percent',
                         {'cpu_stats': {'system_cpu_usage': 100}}, None), 0)

    def test_core_and_domain_import_without_third_party_dependencies(self):
        subprocess.check_call([sys.executable, '-S', '-c',
            'import sys; sys.path.insert(0, sys.argv[1]); '
            'from monit_docker.core import ResourceCalculator; '
            'from monit_docker.domain import ContainerSnapshot',
            str(Path(monit_docker.__file__).resolve().parent.parent)])

    def test_network_and_block_io_are_normalized(self):
        calculator = ResourceCalculator()
        self.assertEqual(calculator.network({'eth0': {'rx_bytes': 10, 'tx_bytes': 20},
                                                 'eth1': {'rx_bytes': 5, 'tx_bytes': 7}}),
                         (15, 27))
        self.assertEqual(calculator.block_io({'io_service_bytes_recursive': [
            {'op': 'Read', 'value': 11}, {'op': 'Write', 'value': 13}]}),
                         (11, 13))


class DomainModelTests(unittest.TestCase):
    def test_snapshot_has_an_explicit_stable_shape(self):
        snapshot = ContainerSnapshot(id='abc', name='web', status='running')
        data = snapshot.to_dict()
        self.assertEqual(tuple(data), ContainerSnapshot.FIELDS)
        self.assertEqual(data['id'], 'abc')
        self.assertIsNone(data['cpu_percent'])

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(TypeError):
            ContainerSnapshot(cpu_precent=80)

    def test_invalid_values_are_rejected(self):
        for values in ({'id': 3}, {'pid': 1.5}, {'mem_usage': '2 GiB'},
                       {'cpu_percent': True}, {'cpu_percent': float('nan')},
                       {'mem_percent': float('inf')}):
            with self.subTest(values=values), self.assertRaises(TypeError):
                ContainerSnapshot(**values)

    def test_snapshot_is_immutable_and_keeps_multicore_cpu(self):
        snapshot = ContainerSnapshot(id='abc', cpu_percent=256)
        self.assertEqual(snapshot.cpu_percent, 256)
        with self.assertRaises(AttributeError):
            snapshot.cpu_percent = 0


if __name__ == '__main__':
    unittest.main()
