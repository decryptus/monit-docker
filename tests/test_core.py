"""Tests for interface-independent domain and metric contracts."""

import unittest

from monit_docker.core import ResourceCalculator
from monit_docker.domain import ContainerSnapshot


class ResourceCalculatorTests(unittest.TestCase):
    def test_raw_values_use_base_units(self):
        calculator = ResourceCalculator()
        memory = {'usage': 1024, 'limit': 4096, 'stats': {'total_cache': 256}}
        self.assertEqual(calculator.memory_usage(memory), 768)
        self.assertEqual(calculator.memory_limit(memory), 4096)
        self.assertEqual(calculator.memory_percent(memory), 18.75)

    def test_human_values_are_only_an_output_concern(self):
        calculator = ResourceCalculator(human_readable=True)
        self.assertEqual(calculator.memory_usage({'usage': 1024, 'limit': 4096}),
                         '1.00 KiB')

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


if __name__ == '__main__':
    unittest.main()
