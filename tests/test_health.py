"""Docker health normalization, rule semantics and transport outputs."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from monit_docker import cli
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor, container_health
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
from monit_docker.core import MonitoringEngine, RuleEvaluator
from monit_docker.domain.errors import RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.outputs.prometheus import render_metrics
from test_monit_docker import container

_DOCKER_STATES = ('healthy', 'unhealthy', 'starting')


def health_container(state='healthy', status='running', test=('CMD', 'true')):
    obj = container()
    obj.status = status
    obj.attrs['Config'] = {'Healthcheck': {'Test': list(test)}}
    if state is not None:
        obj.attrs['State']['Health'] = {'Status': state}
    return obj


class HealthTests(unittest.TestCase):
    def test_absence_disabled_starting_and_unavailable_are_distinct(self):
        self.assertEqual(container_health(container()), 'none')
        self.assertEqual(container_health(health_container(test=('NONE',))), 'none')
        self.assertEqual(container_health(health_container(None)), 'unknown')
        self.assertEqual(container_health(health_container('unexpected')), 'unknown')
        for state in _DOCKER_STATES:
            self.assertEqual(container_health(health_container(state)), state)
        for status in ('exited', 'paused', 'restarting', 'dead'):
            self.assertEqual(container_health(health_container('healthy', status)), 'unknown')

    def test_rules_only_act_on_unhealthy_and_do_not_request_stats_or_exec(self):
        client, executor = Mock(), Mock(return_value=True)
        collector = DockerCollector(lambda: client)
        engine = MonitoringEngine(collector, Mock(execute=executor))
        rule = RuleParser().parse('health == unhealthy ? restart')
        self.assertFalse(rule.needs_metrics)
        for state in ('starting', 'unhealthy', 'healthy', None):
            obj = health_container(state)
            client.containers.list.return_value = [obj]
            result = engine.run_once(rules=(rule,))
            self.assertEqual(len(result.actions), int(state == 'unhealthy'))
            obj.stats.assert_not_called()
            obj.exec_run.assert_not_called()
        executor.assert_called_once()
        client.api.exec_create.assert_not_called()

    def test_membership_aliases_and_invalid_health_conditions(self):
        parser = RuleParser(conditions={'not_ready': {'expr': ['health in (starting,unhealthy)']}})
        evaluator = RuleEvaluator()
        rule = parser.parse('@not_ready ? (true)')
        for state in ('healthy', 'starting', 'unhealthy', 'none', 'unknown'):
            self.assertEqual(evaluator.matches(rule, ContainerSnapshot(health=state)),
                             state in ('starting', 'unhealthy'))
        for expression in ('health == unhealty', 'health > healthy', 'health in healthy',
                           'health == 10', 'health in (unhealthy,)', '1 < health == healthy'):
            with self.subTest(expression=expression), self.assertRaises(RuleSyntaxError):
                parser.parse(expression + ' ? restart')
        with self.assertRaises(TypeError):
            ContainerSnapshot(health='unexpected')

    def test_cli_stats_and_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            base = ['monit-docker', '-c', directory + '/absent.yml', '--logfile', directory + '/missing/log',
                    '--runtimedir', '']
            with patch.object(cli, 'MONIT_DOCKER_CONFIG', None), patch('docker.from_env', return_value=client):
                for state, expected in (('healthy', 0), ('starting', 10), ('unhealthy', 20), (None, 115)):
                    obj = health_container(state)
                    client.containers.list.return_value = [obj]
                    with patch('sys.argv', base + ['monit', '--rsc', 'health']):
                        self.assertEqual(cli.main(cli.argv_parse_check()), expected)
                    obj.stats.assert_not_called()
                client.containers.list.return_value = [container()]
                with patch('sys.argv', base + ['monit', '--rsc', 'health']):
                    self.assertEqual(cli.main(cli.argv_parse_check()), 30)
                with patch('sys.argv', base + ['stats', '--rsc', 'health']), patch('sys.stdout', new_callable=io.StringIO) as output:
                    self.assertEqual(cli.main(cli.argv_parse_check()), 0)
                    self.assertEqual(json.loads(output.getvalue()), {'demo': {'health': 'none'}})

    def test_offline_configuration_validates_health_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.yml'
            config.write_text('conditions:\n  sick:\n    expr: ["health == unhealthy"]\n')
            check_configuration(str(config), expressions=['@sick ? restart'])
            with self.assertRaises(ConfigurationCheckError):
                check_configuration(str(config), expressions=['health == broken ? restart'])

    def test_prometheus_reports_current_state_without_healthcheck_output(self):
        obj = health_container('unhealthy')
        obj.attrs['State']['Health']['Log'] = [{'Output': 'sensitive application output'}]
        client = Mock()
        client.containers.list.return_value = [obj]
        collector = DockerCollector(lambda: client)
        snapshot = MonitoringEngine(collector, DockerActionExecutor(collector)).run_once(resources=('health',)).snapshots[0]
        data = dict(ready=True, running=False, cycles_total=1, errors_total=0,
                    last_success_at=None, actions={}, containers=[snapshot.to_dict()])
        metrics = render_metrics(data)
        self.assertIn('monit_docker_container_health_status{id="demo",name="demo",health="unhealthy"} 1', metrics)
        self.assertNotIn('sensitive application output', json.dumps(snapshot.to_dict()))
        data['ready'] = False
        self.assertNotIn('health="unhealthy"', render_metrics(data))


if __name__ == '__main__':
    unittest.main()
