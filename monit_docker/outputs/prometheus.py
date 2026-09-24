"""Prometheus text exposition 0.0.4 from the service cache (no collection)."""

from monit_docker.domain.filesystems import FILESYSTEM_ACCESS_FIELDS

_FILESYSTEM_METRICS = (
    ('disk_usage', 'disk_usage_bytes'), ('disk_available', 'disk_available_bytes'),
    ('disk_total', 'disk_total_bytes'), ('disk_percent', 'disk_usage_percent'),
    ('inode_usage', 'inode_usage'), ('inode_available', 'inode_available'),
    ('inode_total', 'inode_total'), ('inode_percent', 'inode_usage_percent'))
_RUNTIME_METRICS = (
    ('pids_current', 'Current processes and threads in the container cgroup.'),
    ('pids_limit', 'Configured finite container PID limit; absent when unlimited.'),
    ('pids_percent', 'Processes and threads as a percentage of the configured PID limit.'),
    ('event_history_complete', 'Requested window fits the retained daemon event buffer; not durable across daemon restarts.'),
    ('event_window_end', 'Unix timestamp of the sampled Docker event cutoff.'))
_EVENT_METRICS = (
    ('oom_events', 'Docker OOM events in the configured recent window.'),
    ('starts_recent', 'Docker start events in the recent window, including the initial start.'))

def _label(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')


def render_metrics(data):
    lines = []

    def metric(name, kind, help_text, samples):
        name = 'monit_docker_' + name
        lines.extend(('# HELP %s %s' % (name, help_text), '# TYPE %s %s' % (name, kind)))
        for labels, value in samples:
            suffix = ('{' + ','.join('%s="%s"' % (k, _label(v)) for k, v in labels) + '}') if labels else ''
            lines.append('%s%s %s' % (name, suffix, value))

    for name, kind, description, value in (
            ('ready', 'gauge', 'Latest cycle succeeded and its data is fresh.', int(data['ready'])),
            ('cycle_running', 'gauge', 'A monitoring cycle is running.', int(data['running'])),
            ('cycles_total', 'counter', 'Completed monitoring cycles since process start.', data['cycles_total']),
            ('cycle_errors_total', 'counter', 'Failed cycles since process start.', data['errors_total'])):
        metric(name, kind, description, [((), value)])
    if data['last_success_at'] is not None:
        metric('last_success_timestamp_seconds', 'gauge', 'Unix time of last successful cycle.',
               [((), data['last_success_at'])])
    metric('action_decisions_total', 'counter', 'Reported action decisions since process start.',
           [((('outcome', k),), v) for k, v in sorted(data['actions'].items())])
    containers = data['containers'] if data['ready'] else []
    labels = lambda c: (('id', c['id']), ('name', c['name']))
    metric('container_info', 'gauge', 'Container identity and Docker status.',
           [(labels(c) + (('status', c['status']),), 1) for c in containers])
    metric('container_health_status', 'gauge', 'Container health state; the current state has value 1.',
           [(labels(c) + (('health', c.get('health') or 'unknown'),), 1) for c in containers])
    for field, description in (
            ('restart_attempts', 'Automatic restart attempts reserved since explicit rearm.'),
            ('restart_limit', 'Configured automatic restart attempt limit.')):
        metric('container_' + field, 'gauge', description,
               [(labels(c), c[field]) for c in containers if c.get(field) is not None])
    metric('container_restart_blocked', 'gauge', 'Automatic restart budget exhausted; explicit rearm required.',
           [(labels(c), int(c['restart_attempts'] >= c['restart_limit'])) for c in containers
            if c.get('restart_limit') is not None and c.get('restart_attempts') is not None])
    metric('container_maintenance_active', 'gauge', 'Automatic rule actions suspended by maintenance at the last cycle.',
           [(labels(c), int(c.get('maintenance_active', False))) for c in containers])
    metric('container_maintenance_until_seconds', 'gauge', 'Unix time when maintenance ends.',
           [(labels(c), c['maintenance_until']) for c in containers if c.get('maintenance_until') is not None])
    for field, description in _RUNTIME_METRICS:
        metric('container_' + field, 'gauge', description,
               [(labels(c), c[field]) for c in containers if c.get(field) is not None])
    for field, description in _EVENT_METRICS:
        metric('container_' + field, 'gauge', description,
               [(labels(c) + (('window_seconds', c['event_window_seconds']),), c[field])
                for c in containers if c.get(field) is not None])
    for field, name, kind in (
            ('mem_usage', 'memory_usage_bytes', 'gauge'),
            ('mem_limit', 'memory_limit_bytes', 'gauge'),
            ('mem_percent', 'memory_usage_percent', 'gauge'),
            ('cpu_percent', 'cpu_usage_percent', 'gauge'),
            ('io_read', 'io_read_bytes_total', 'counter'),
            ('io_write', 'io_write_bytes_total', 'counter'),
            ('net_rx', 'network_receive_bytes_total', 'counter'),
            ('net_tx', 'network_transmit_bytes_total', 'counter')):
        metric('container_' + name, kind, 'Docker container ' + name.replace('_', ' ') + '.',
               [(labels(c), c[field]) for c in containers if c[field] is not None])
    for field, name in _FILESYSTEM_METRICS:
        metric('container_' + name, 'gauge', 'Container filesystem ' + name.replace('_', ' ') + '.',
               [(labels(c) + (('group', sample['group']), ('path', sample['path'])), sample[field])
                for c in containers for sample in c.get('filesystems', ()) if sample[field] is not None])
    metric('container_filesystem_read_only', 'gauge', 'Filesystem mount is read-only (1) or read-write (0).',
           [(labels(c) + (('group', sample['group']), ('path', sample['path'])), int(sample['fs_mode'] == 'ro'))
            for c in containers for sample in c.get('filesystems', ()) if sample.get('fs_mode') in ('ro', 'rw')])
    for field in FILESYSTEM_ACCESS_FIELDS:
        metric('container_' + field, 'gauge', 'Kernel access check for the configured probe identity (1 allowed, 0 denied).',
               [(labels(c) + (('group', sample['group']), ('path', sample['path'])), sample[field])
                for c in containers for sample in c.get('filesystems', ()) if sample.get(field) is not None])
    return '\n'.join(lines) + '\n'
