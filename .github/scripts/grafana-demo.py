"""Generate synthetic Prometheus history and export real Grafana PNGs for docs.

No Docker host or production metrics are read. Run through grafana-screenshots.yml.
"""

import base64
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import struct
import sys
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DASHBOARD = Path('examples/grafana/monit-docker.json')


def prepare(directory):
    directory.mkdir(parents=True, exist_ok=True)
    end = int(time.time() // 30) * 30 - 120
    start = end - 3600
    series = {}

    def add(name, labels, value, timestamp):
        key = name + '{' + ','.join(k + '=' + json.dumps(v)
                                  for k, v in sorted(labels.items())) + '}'
        series.setdefault(key, []).append((timestamp, value))

    for i, timestamp in enumerate(range(start - 600, end + 1, 30)):
        labels = {'job': 'monit-docker-demo', 'instance': 'demo-host:9808'}
        values = {'up': 1, 'monit_docker_ready': 1,
                  'monit_docker_cycle_running': 0, 'monit_docker_cycles_total': 800 + i,
                  'monit_docker_cycle_errors_total': int(i >= 50) + int(i >= 85),
                  'monit_docker_last_success_timestamp_seconds': timestamp - 4}
        for name, value in values.items():
            add(name, labels, value, timestamp)
        outcomes = {'executed': int(i >= 35) + int(i >= 72) + int(i >= 105),
                    'cooldown': max(0, min(i - 35, 8)) + max(0, min(i - 72, 6)),
                    'dry-run': 0}
        for outcome, value in outcomes.items():
            add('monit_docker_action_decisions_total', dict(labels, outcome=outcome), value, timestamp)
        for n, name in enumerate(('web-frontend', 'api', 'worker', 'postgres', 'redis', 'backup')):
            container = dict(labels, id='%064x' % (n + 1), name=name)
            add('monit_docker_container_info',
                dict(container, status='exited' if n == 5 else 'running'), 1, timestamp)
            if n == 5:
                continue
            cpu = ((12, 28, 42, 18, 5)[n] + (5, 11, 22, 6, 2)[n] * math.sin(i / 7 + n)
                   + (4, 10, 40, 8, 1)[n] * math.exp(-((i - 80) / 9) ** 2))
            memory = (22, 37, 46, 63, 13)[n] + (2, 4, 9, 3, 1)[n] * math.sin(i / 14 + n)
            limit = (512, 1024, 1024, 2048, 256)[n] * 1024 ** 2
            resources = {
                'cpu_usage_percent': cpu, 'memory_usage_percent': memory,
                'memory_usage_bytes': limit * memory / 100, 'memory_limit_bytes': limit,
                'network_receive_bytes_total': 1e8 + (n + 1) * 2e5 * (i * 30 + 30 * math.sin(i / 8 + n)),
                'network_transmit_bytes_total': 1e8 + (n + 1) * 1.2e5 * (i * 30 + 25 * math.sin(i / 9 + n)),
                'io_read_bytes_total': 1e8 + (n + 1) * 8e4 * (i * 30 + 30 * math.sin(i / 6 + n)),
                'io_write_bytes_total': 1e8 + (n + 1) * 5e4 * (i * 30 + 25 * math.sin(i / 10 + n)),
            }
            for metric, value in resources.items():
                add('monit_docker_container_' + metric, container, value, timestamp)
    with (directory / 'demo.openmetrics').open('w') as stream:
        for key, samples in sorted(series.items()):
            for timestamp, value in samples:
                stream.write('%s %.6f %s\n' % (key, value, timestamp))
        stream.write('# EOF\n')
    (directory / 'range.json').write_text(json.dumps({'from': start * 1000, 'to': end * 1000}))
    (directory / 'prometheus.yml').write_text('global:\n  scrape_interval: 30s\nscrape_configs: []\n')
    provisioning = directory / 'provisioning'
    for name in ('datasources', 'dashboards', 'plugins', 'alerting'):
        (provisioning / name).mkdir(parents=True, exist_ok=True)
    (directory / 'dashboards').mkdir(exist_ok=True)
    (provisioning / 'datasources/demo.yml').write_text('''apiVersion: 1
datasources:
  - name: Prometheus Demo
    uid: prometheus-demo
    type: prometheus
    access: proxy
    url: http://demo-prometheus:9090
    isDefault: true
    jsonData:
      timeInterval: 30s
''')
    (provisioning / 'dashboards/demo.yml').write_text('''apiVersion: 1
providers:
  - name: demo
    type: file
    options:
      path: /demo-dashboards
''')
    dashboard = json.loads(DASHBOARD.read_text())
    dashboard['title'] = 'monit-docker — DEMO DATA'
    dashboard['description'] = 'Synthetic demonstration data, not production measurements.'
    dashboard['refresh'] = ''
    dashboard['time'] = {k: datetime.fromtimestamp(v, timezone.utc).isoformat()
                         for k, v in {'from': start, 'to': end}.items()}
    dashboard['templating']['list'][0]['current'] = {'text': 'Prometheus Demo', 'value': 'prometheus-demo'}
    (directory / 'dashboards/demo.json').write_text(json.dumps(dashboard))
    print('Prepared %s synthetic time series.' % len(series))


def render(directory):
    # Credentials belong only to the disposable CI containers.
    auth = 'Basic ' + base64.b64encode(b'admin:monit-demo-only').decode()

    def grafana(path, timeout=10):
        with urlopen(Request('http://127.0.0.1:3000' + path,
                             headers={'Authorization': auth}), timeout=timeout) as response:
            return response.read()

    deadline = time.monotonic() + 120
    while True:
        try:
            grafana('/api/dashboards/uid/monit-docker-overview')
            break
        except (URLError, OSError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)
    bounds = json.loads((directory / 'range.json').read_text())
    dashboard = json.loads(DASHBOARD.read_text())
    # Fail before rendering if any panel query has no data or invalid PromQL.
    for panel in dashboard['panels']:
        for target in panel['targets']:
            expression = target['expr']
            for variable in ('job', 'instance', 'container'):
                expression = expression.replace('${%s:regex}' % variable, '.*')
            expression = expression.replace('$__rate_interval', '5m')
            query = urlencode({'query': expression, 'time': bounds['to'] / 1000})
            with urlopen('http://127.0.0.1:9090/api/v1/query?' + query, timeout=10) as response:
                result = json.load(response)
            assert result['status'] == 'success' and result['data']['result'], panel['title']
    output = directory / 'screenshots'
    output.mkdir(exist_ok=True)
    for name, panel, height in (('overview', None, 1800), ('cpu', 7, 650), ('network', 10, 650)):
        params = dict(bounds, width=1600, height=height, theme='dark', tz='UTC', timeout=120,
                      **{'var-datasource': 'prometheus-demo', 'var-job': 'monit-docker-demo',
                         'var-instance': 'demo-host:9808', 'var-container': '$__all'})
        route = '/render/d/monit-docker-overview/demo'
        if panel is not None:
            params['panelId'] = panel
            route = '/render/d-solo/monit-docker-overview/demo'
        image = grafana(route + '?' + urlencode(params), timeout=150)
        assert image.startswith(b'\x89PNG\r\n\x1a\n') and len(image) > 10000, name
        width, actual_height = struct.unpack('>II', image[16:24])
        assert width >= 1200 and actual_height >= 500, (width, actual_height)
        (output / ('grafana-%s.png' % name)).write_bytes(image)
        print('Rendered %s: %s x %s (%s bytes)' % (name, width, actual_height, len(image)))
    (output / 'README.txt').write_text(
        'Real Grafana 12.2.0 renders of examples/grafana/monit-docker.json.\n'
        'All data is synthetic demonstration data, not production measurements or benchmarks.\n'
        'Panels and queries are unchanged; the demo title, time range and datasource selection are set for capture.\n')


if __name__ == '__main__':
    action, path = sys.argv[1:]
    if action == 'prepare':
        prepare(Path(path))
    elif action == 'render':
        render(Path(path))
    else:
        raise ValueError('expected prepare or render')
