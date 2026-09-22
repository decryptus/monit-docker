"""Validate sample PromQL with promtool and import the dashboard into CI Grafana."""

import base64
import json
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

dashboard = json.loads(Path('examples/grafana/monit-docker.json').read_text())

if sys.argv[1] == 'rules':
    rules = []
    for panel in dashboard['panels']:
        for target in panel['targets']:
            expression = target['expr']
            for variable in ('job', 'instance', 'container'):
                expression = expression.replace('${%s:regex}' % variable, '.*')
            expression = expression.replace('$__rate_interval', '5m')
            rules.append({'record': 'example_panel_%s_%s' % (panel['id'], target['refId']),
                          'expr': expression})
    Path(sys.argv[2]).write_text(json.dumps({'groups': [{'name': 'grafana', 'rules': rules}]}))
elif sys.argv[1] == 'import':
    # This fixed credential belongs only to the disposable CI container.
    auth = base64.b64encode(b'admin:monit-ci-only').decode()
    def api(path, payload=None):
        request = Request('http://127.0.0.1:3000' + path,
                          data=None if payload is None else json.dumps(payload).encode(),
                          headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'})
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    deadline = time.monotonic() + 60
    while True:
        try:
            if api('/api/health')['database'] == 'ok':
                break
        except (URLError, OSError):
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Grafana did not become ready')
        time.sleep(0.5)
    result = api('/api/dashboards/db', {'dashboard': dashboard, 'overwrite': False})
    assert result['status'] == 'success', result
    restored = api('/api/dashboards/uid/' + dashboard['uid'])['dashboard']
    assert len(restored['panels']) == 14
    assert [p['targets'] for p in restored['panels']] == [p['targets'] for p in dashboard['panels']]
    print('Grafana accepted the dashboard and preserved all panel queries.')
else:
    raise ValueError('expected rules or import')
