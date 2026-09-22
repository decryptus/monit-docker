"""Pure resource calculations shared by cron, HTTP and Prometheus outputs."""

from __future__ import absolute_import


class ResourceCalculator(object):
    """Convert Docker stats samples into normalized resource values."""

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

    def memory_usage(self, data):
        return self._memory_usage_bytes(data)

    def memory_limit(self, data):
        return data.get('limit', 0)

    def network(self, data):
        received = 0
        transmitted = 0
        if data:
            for value in data.values():
                received += value['rx_bytes']
                transmitted += value['tx_bytes']
        return received, transmitted

    def block_io(self, data):
        read = 0
        written = 0
        if data and data.get('io_service_bytes_recursive'):
            for value in data['io_service_bytes_recursive']:
                if value['op'] == 'Read':
                    read += value['value']
                elif value['op'] == 'Write':
                    written += value['value']
        return read, written

    def get(self, resource, current, previous):
        if resource == 'mem_usage':
            return self.memory_usage(current['memory_stats'])
        if resource == 'mem_limit':
            return self.memory_limit(current['memory_stats'])
        if resource == 'mem_percent':
            return self.memory_percent(current['memory_stats'])
        if resource == 'cpu_percent':
            return self.cpu_percent(current['cpu_stats'], (previous or {}).get('cpu_stats'))
        if resource == 'io_read':
            return self.block_io(current.get('blkio_stats'))[0]
        if resource == 'io_write':
            return self.block_io(current.get('blkio_stats'))[1]
        if resource == 'net_rx':
            return self.network(current.get('networks'))[0]
        if resource == 'net_tx':
            return self.network(current.get('networks'))[1]
        raise KeyError(resource)
