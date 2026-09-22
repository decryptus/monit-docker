"""Exercise the shipped Compose stack against a real Docker fixture in CI."""

import base64
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / 'examples/monitoring'


def request(url, payload=None, auth=None):
    headers = {'Content-Type': 'application/json'}
    if auth:
        headers['Authorization'] = auth
    data = None if payload is None else json.dumps(payload).encode()
    with urlopen(Request(url, data=data, headers=headers), timeout=10) as response:
        return json.load(response)


def wait_for(check, description, timeout=150):
    deadline = time.monotonic() + timeout
    while True:
        try:
            result = check()
            if result:
                print('Verified: ' + description, flush=True)
                return result
        except (URLError, OSError, KeyError):
            pass
        if time.monotonic() >= deadline:
            raise AssertionError('Timed out: ' + description)
        time.sleep(2)


def main():
    fixture_id = subprocess.check_output(
        ['docker', 'inspect', '--format', '{{.Id}}', os.environ['MONIT_COMPOSE_FIXTURE']],
        text=True).strip()
    credential_file = STACK / '.env'
    original_env = credential_file.read_bytes()
    assert credential_file.stat().st_mode & 0o777 == 0o600
    password = dict(line.split('=', 1) for line in original_env.decode().splitlines()
                    if line and not line.startswith('#'))['GRAFANA_ADMIN_PASSWORD']
    assert len(password) == 48
    auth = 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode()
    grafana = 'http://127.0.0.1:3000'
    prometheus = 'http://127.0.0.1:9090'
    info_query = 'monit_docker_container_info{id="%s"}' % fixture_id

    def query(expression, at=None, through_grafana=False):
        params = {'query': expression}
        if at is not None:
            params['time'] = at
        base = (grafana + '/api/datasources/proxy/uid/monit-docker-prometheus'
                if through_grafana else prometheus)
        result = request(base + '/api/v1/query?' + urlencode(params),
                         auth=auth if through_grafana else None)
        assert result['status'] == 'success', expression
        return result['data']['result']

    def collected():
        status = request('http://127.0.0.1:9808/v1/status')
        return status['ready'] and any(c['id'] == fixture_id and c['mem_usage'] is not None
                                       for c in status['containers'])

    def alert_rules():
        result = request(prometheus + '/api/v1/rules?type=alert')
        assert result['status'] == 'success'
        groups = [g for g in result['data']['groups'] if g['name'] == 'monit-docker']
        assert len(groups) == 1, 'Expected the shipped alert group'
        assert groups[0]['interval'] == 30
        rules = {r['name']: r for r in groups[0]['rules']}
        assert {name: rule['duration'] for name, rule in rules.items()} == {
            'MonitDockerAgentDown': 120,
            'MonitDockerCollectionNotReady': 180,
            'MonitDockerContainerMemoryHigh': 300,
        }
        return rules

    def rules_healthy():
        return all(r['health'] == 'ok' and not r.get('lastError')
                   for r in alert_rules().values())

    wait_for(collected, 'serve collected the real fixture')
    wait_for(rules_healthy, 'all three alert rules are loaded and evaluate successfully')
    wait_for(lambda: query(info_query), 'Prometheus scraped the fixture')
    wait_for(lambda: query('monit_docker_container_memory_usage_bytes{id="%s"}' % fixture_id,
                           through_grafana=True), 'Grafana queries real memory measurements')
    samples = 'count_over_time(monit_docker_container_info{id="%s"}[5m]) >= 2' % fixture_id
    wait_for(lambda: query(samples), 'multiple scrapes are stored')

    dashboard = request(grafana + '/api/dashboards/uid/monit-docker-overview', auth=auth)
    expected = json.loads((ROOT / 'examples/grafana/monit-docker.json').read_text())
    assert [p['targets'] for p in dashboard['dashboard']['panels']] == [p['targets'] for p in expected['panels']]
    assert dashboard['meta']['provisioned']
    print('Verified: the original dashboard is provisioned with every query intact', flush=True)
    request(grafana + '/api/folders', {'uid': 'compose-persistence-probe', 'title': 'Persistence probe'}, auth)

    historical_time = time.time()
    assert query(info_query, at=historical_time)
    subprocess.run(['docker', 'compose', 'stop', 'monit-docker'], cwd=STACK, check=True)
    wait_for(lambda: query('up{job="monit-docker"} == 0'),
             'Prometheus detects an unavailable agent while Grafana stays online')
    assert request(grafana + '/api/health')['database'] == 'ok'
    wait_for(lambda: alert_rules()['MonitDockerAgentDown']['state'] in ('pending', 'firing'),
             'the loaded agent-down rule detects the stopped agent')
    assert alert_rules()['MonitDockerCollectionNotReady']['state'] == 'inactive'

    subprocess.run(['docker', 'compose', 'down'], cwd=STACK, check=True)
    subprocess.run(['sh', 'start.sh'], cwd=STACK, check=True)
    assert credential_file.read_bytes() == original_env, 'Startup changed the password file'
    assert query(info_query, at=historical_time), 'Prometheus history was lost'
    folder = request(grafana + '/api/folders/compose-persistence-probe', auth=auth)
    assert folder['title'] == 'Persistence probe', 'Grafana database was lost'
    wait_for(collected, 'collection resumes after recreation')
    wait_for(lambda: query('up{job="monit-docker"} == 1'), 'scraping recovers after recreation')
    wait_for(lambda: alert_rules()['MonitDockerAgentDown']['state'] == 'inactive',
             'the agent-down alert clears after recovery')
    assert rules_healthy()
    print('Verified: history, Grafana data and credentials survive down/up', flush=True)


if __name__ == '__main__':
    main()
