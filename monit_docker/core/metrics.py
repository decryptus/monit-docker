"""Pure resource calculations shared by cron, HTTP and Prometheus outputs."""

from __future__ import absolute_import

import bitmath
import six


class ResourceCalculator(object):
    """Convert Docker stats samples into normalized resource values."""

    def __init__(self, human_readable=False, logger=None):
        self.human_readable = human_readable
        self.logger = logger

    @staticmethod
    def cpu_percent(current, previous):
        if not current.get('system_cpu_usage'):
            return 0.0
        if not previous or not previous.get('system_cpu_usage'):
            return 0.0

        cpu_delta = (float(current['cpu_usage']['total_usage']) -
                     float(previous['cpu_usage']['total_usage']))
        system_delta = (float(current['system_cpu_usage']) -
                        float(previous['system_cpu_usage']))
        if current.get('online_cpus'):
            online_cpus = current['online_cpus']
        elif current['cpu_usage'].get('percpu_usage'):
            online_cpus = len(current['cpu_usage']['percpu_usage'])
        else:
            return 0.0
        if cpu_delta > 0.0 and system_delta > 0.0:
            return round((cpu_delta / system_delta) * online_cpus * 100.0, 2)
        return 0.0

    @staticmethod
    def memory_percent(data):
        if not data.get('limit'):
            return 0.0
        usage = ResourceCalculator._memory_usage_bytes(data)
        return round(float(usage) / float(data['limit']) * 100.0, 2)

    @staticmethod
    def _memory_usage_bytes(data):
        usage = data.get('usage', 0)
        if data.get('stats') and 'total_cache' in data['stats']:
            usage -= data['stats']['total_cache']
        return usage

    @staticmethod
    def _format_bytes(value, decimals=2, minimum_kb=False):
        if value < 1:
            result = bitmath.Byte(value)
        elif minimum_kb:
            result = bitmath.Byte(value).to_kB().best_prefix()
        else:
            result = bitmath.Byte(value).best_prefix()
        template = '{value:.%df} {unit}' % decimals
        return result.format(template).replace('Byte', 'B')

    def memory_usage(self, data):
        value = self._memory_usage_bytes(data)
        return self._format_bytes(value) if self.human_readable else value

    def memory_limit(self, data):
        value = data.get('limit', 0)
        return self._format_bytes(value) if self.human_readable else value

    def network(self, data):
        received = 0
        transmitted = 0
        if data:
            for value in six.itervalues(data):
                received += value['rx_bytes']
                transmitted += value['tx_bytes']
        if not self.human_readable:
            return received, transmitted
        return (self._format_bytes(received, decimals=1, minimum_kb=True),
                self._format_bytes(transmitted, decimals=1, minimum_kb=True))

    def block_io(self, data):
        read = 0
        written = 0
        if data and data.get('io_service_bytes_recursive'):
            for value in data['io_service_bytes_recursive']:
                if value['op'] == 'Read':
                    read += value['value']
                elif value['op'] == 'Write':
                    written += value['value']
        if not self.human_readable:
            return read, written
        return (self._format_bytes(read, decimals=1, minimum_kb=True),
                self._format_bytes(written, decimals=1, minimum_kb=True))

    def get(self, resource, current, previous):
        if resource == 'mem_usage':
            return self.memory_usage(current['memory_stats'])
        if resource == 'mem_limit':
            return self.memory_limit(current['memory_stats'])
        if resource == 'mem_percent':
            return self.memory_percent(current['memory_stats'])
        if resource == 'cpu_percent':
            return self.cpu_percent(current['cpu_stats'], previous.get('cpu_stats'))
        if resource == 'io_read':
            return self.block_io(current.get('blkio_stats'))[0]
        if resource == 'io_write':
            return self.block_io(current.get('blkio_stats'))[1]
        if resource == 'net_rx':
            return self.network(current.get('networks'))[0]
        if resource == 'net_tx':
            return self.network(current.get('networks'))[1]
        raise KeyError(resource)
