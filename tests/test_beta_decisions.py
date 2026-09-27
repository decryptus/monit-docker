"""Approved beta behavior, verified without real Docker mutations."""
import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.docker import client_factory
from monit_docker.adapters.scenarios import validate_scenario
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
from monit_docker.composition import build_application
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.job import JobOptions

_OPERATORS = ('<', '<=', '>', '>=', '==', '!=')


class BetaDecisionTests(unittest.TestCase):
    def test_chains_match_python_for_every_operator_pair_and_boundaries(self):
        parser, evaluator = RuleParser(), RuleEvaluator()
        for left, right, value in itertools.product(_OPERATORS, _OPERATORS, (0, 10, 10.5, 50, 90, 100)):
            expression = '10 %s cpu_percent %s 90' % (left, right)
            with self.subTest(expression=expression, value=value):
                expected = eval(expression, {'__builtins__': {}}, {'cpu_percent': value})
                rule = parser.parse(expression + ' ? restart')
                self.assertEqual(evaluator.matches(rule, ContainerSnapshot(cpu_percent=value)), expected)

    def test_chains_with_units_and_aliases(self):
        parser = RuleParser(conditions={'range': {'expr': ['1 KiB <= mem_usage < 2 KiB']}})
        rule = parser.parse('@range ? restart')
        for value, expected in ((1023, False), (1024, True), (2047, True), (2048, False)):
            self.assertEqual(RuleEvaluator().matches(rule, ContainerSnapshot(mem_usage=value)), expected)

    def test_commas_are_literal_for_globs_and_preserved_in_regex(self):
        for pattern, value, expected in (
                ('web,api', 'web', False), ('web,api', 'web,api', True),
                ('~worker-[0-9]{1,3}$', 'worker-123', True),
                ('~worker-[0-9]{1,3}$', 'worker-1234', False),
                ('~café$', 'café', True), ('~web$', 'web\n', True)):
            selector = ContainerSelector({'name': [pattern]})
            self.assertEqual(selector.matches('id', value, 'running', (), ()), expected)

    def test_group_union_intersects_direct_union_and_status(self):
        config = {'ctn-groups': {'prod': {'match': ['label:prod']}, 'stage': {'match': ['label:stage']}},
                  'scenarios': {'selected': {'mode': 'stats', 'select': {
                      'group': ['prod', 'stage'], 'name': ['web-*', 'api-*'], 'status': 'running'}}}}
        job = validate_scenario(config, 'selected')
        selector = ContainerSelector({'name': job.name}, job.status, config['ctn-groups'], job.ctn_grp)
        for name, label, status, expected in (
                ('web-1', 'prod', 'running', True), ('api-1', 'stage', 'running', True),
                ('db-1', 'prod', 'running', False), ('web-1', 'other', 'running', False),
                ('web-1', 'prod', 'exited', False)):
            self.assertEqual(selector.matches('id', name, status, (label,), ()), expected)
        self.assertTrue(ContainerSelector(groups=config['ctn-groups'], selected_groups=['prod']).matches(
            'id', 'db-1', 'running', ('prod',), ()))
        self.assertTrue(ContainerSelector({'name': ['web-*']}).matches('id', 'web-1', 'running', (), ()))

    def test_unknown_client_never_falls_back_even_with_environment_option(self):
        for config in ({}, {'clients': {}}, {'clients': {'local': {'config': {}}}}):
            for from_env in (False, True):
                with patch('monit_docker.adapters.docker.docker.from_env') as connect:
                    with self.assertRaises(MonitoringError) as error:
                        client_factory(config, 'missing', from_env)
                    self.assertEqual(error.exception.code, 110)
                    connect.assert_not_called()

    def test_invalid_yaml_is_rejected_by_inspection_and_every_runtime_mode(self):
        invalid = ('unknown: {}', 'commands: {bad: {exec: [restart], typo: 1}}',
                   'commands: {bad: {exec: []}}', 'clients: {bad: {config: []}}',
                   'scenarios: {bad: {mode: stats, all: true, max-restart: 3}}',
                   'scenarios: {bad: {mode: stats, all: true, resources: [unknown]}}',
                   'commands: {"bad\\n": {exec: [restart]}}',
                   'commands: {"é": {exec: [restart]}}',
                   'commands: {"' + 'a' * 66 + '": {exec: [restart]}}',
                   'commands: [', '[]')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yml'
            for source in invalid:
                path.write_text(source)
                with self.subTest(source=source):
                    with self.assertRaises(ConfigurationCheckError):
                        check_configuration(str(path))
                    for mode in ('stats', 'monit', 'cron', 'serve'):
                        job = JobOptions(subcommand=mode, conffile=str(path),
                                         cmd=('restart',) if mode == 'cron' else (),
                                         state_file=str(Path(directory) / 'state'))
                        with patch('monit_docker.composition.client_factory') as factory:
                            with self.assertRaises(MonitoringError) as error:
                                build_application(job, use_rules=mode != 'stats')
                            self.assertEqual(error.exception.code, 110)
                            factory.assert_not_called()

    def test_missing_configuration_remains_valid_for_simple_usage(self):
        with patch('monit_docker.composition.client_factory'):
            self.assertIsNotNone(build_application(JobOptions(conffile='/nonexistent/config')))

    def test_pathological_regex_is_bounded_and_fails_explicitly(self):
        selector = ContainerSelector({'name': ['~(a+)+$']})
        with self.assertRaises(MonitoringError) as error:
            selector.matches('id', 'a' * 10000 + '!', 'running', (), ())
        self.assertEqual(error.exception.code, 110)
        for pattern in ('~[', '~' + 'a' * 4096):
            with self.assertRaises(Exception):
                ContainerSelector({'name': [pattern]})
