"""Guard against drift between exposed metrics, reference docs and Grafana."""

import json
from pathlib import Path
import re
import unittest

from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.outputs.prometheus import render_metrics
from monit_docker.service import MonitorService

ROOT = Path(__file__).resolve().parents[2]


class MonitoringExamplesTests(unittest.TestCase):
    def test_every_dashboard_metric_is_exposed_and_every_metric_is_documented(self):
        values = dict((field, 1) for field in ContainerSnapshot.RESOURCE_FIELDS if field not in ('status', 'health'))
        values['health'] = 'healthy'
        snapshot = ContainerSnapshot(id='one', name='one', status='running',
                                     manual_actions_protected=True, **values)
        monitor = MonitorService(lambda observer: CycleResult((snapshot,), ()))
        monitor.run_cycle()
        exposed = set(re.findall(r'^# TYPE (\w+) ', render_metrics(monitor.status()), re.M))
        dashboard = json.loads((ROOT / 'examples/grafana/monit-docker.json').read_text())
        expressions = '\n'.join(target['expr'] for p in dashboard['panels'] for target in p['targets'])
        used = set(re.findall(r'\bmonit_docker_\w+', expressions))
        self.assertTrue(used)
        self.assertEqual(used - exposed, set())
        docs = (ROOT / 'docs/metrics.md').read_text()
        for metric in exposed:
            with self.subTest(metric=metric):
                self.assertIn('`' + metric + '`', docs)


if __name__ == '__main__':
    unittest.main()
