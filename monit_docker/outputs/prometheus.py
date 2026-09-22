"""Prometheus text exposition 0.0.4 from the service cache (no collection)."""


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
    return '\n'.join(lines) + '\n'
