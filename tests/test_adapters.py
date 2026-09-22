"""Configuration, selection and normalization compatibility boundaries."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.docker import client_factory
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot


class AdapterTests(unittest.TestCase):
    def test_configuration_relative_imports_templates_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'commands.yml').write_text(
                'act:\n  exec:\n    - restart:\n        kwargs:\n'
                '          timeout: ${vars["timeout"]}\n')
            (root / 'config.yml').write_text(
                'vars:\n  timeout: 7\ncommands:\n  "@import_command": commands.yml\n'
                'ctn-groups:\n  web:\n    vars:\n      prefix: web\n'
                '    match: ["name:${vars[\'prefix\']}*"]\n')
            config = Configuration(str(root / 'config.yml')).load()
        action = RuleParser(commands=config['commands']).parse('@act').actions[0]
        self.assertEqual(action.kwargs, {'timeout': 7})
        selector = ContainerSelector(groups=config['ctn-groups'], selected_groups=['web'])
        self.assertTrue(selector.matches('id', 'web-1', 'running', (), ()))
        self.assertFalse(selector.matches('id', 'db', 'running', (), ()))

    def test_stats_does_not_load_unused_rules(self):
        config = Configuration('/nonexistent/config', 'commands:\n  broken: {}\n')
        self.assertEqual(config.load(include_rules=False), {})
        with self.assertRaises(MonitoringError) as error:
            config.load()
        self.assertEqual(error.exception.code, 110)

    def test_configuration_reloading_does_not_keep_previous_variables(self):
        config = Configuration('/nonexistent/config', 'vars: {old: value}\n')
        config.load()
        config.inline = '{}'
        config.load()
        self.assertEqual(config._common_conf['vars'], {})

    def test_invalid_configuration_is_a_configuration_error(self):
        for content in ('', '[]', 'null'):
            # An empty inline string means no configuration, as before.
            if content:
                with self.assertRaises(MonitoringError):
                    Configuration('/nonexistent/config', content).load()

    def test_selected_groups_override_cli_selectors_and_status_still_filters(self):
        selector = ContainerSelector(selectors={'name': ['db']}, statuses=['running'],
                                     groups={'web': {'match': ['name:web*']}}, selected_groups=['web'])
        self.assertTrue(selector.matches('id', 'web-1', 'running', (), ()))
        self.assertFalse(selector.matches('id', 'db', 'running', (), ()))
        self.assertFalse(selector.matches('id', 'web-1', 'exited', (), ()))

    def test_label_values_image_tags_regex_and_comma_selectors(self):
        for selectors in ({'label': ['front*']}, {'image': ['org/web:*']},
                          {'name': ['~web-[0-9]+']}, {'name': ['db, web-1']}):
            with self.subTest(selectors=selectors):
                selector = ContainerSelector(selectors=selectors)
                self.assertTrue(selector.matches('id', 'web-1', 'running',
                                                 ['frontend'], ['org/web:1']))
                self.assertFalse(selector.matches('id', 'other', 'running', [], []))

    def test_client_settings_are_fresh_each_cycle_and_tls_config_is_local(self):
        config = {'clients': {'local': {'config': {'base_url': 'tcp://example:2376',
                                                  'tls': {'verify': False}}}}}
        tls = Mock()
        with patch('monit_docker.adapters.docker.docker.tls.TLSConfig', return_value=tls) as make_tls:
            with patch('monit_docker.adapters.docker.docker.DockerClient') as make_client:
                connect = client_factory(config)
                connect()
                connect()
        self.assertEqual(make_tls.call_count, 2)
        make_client.assert_called_with(base_url='tcp://example:2376', tls=tls)
        self.assertEqual(config['clients']['local']['config']['tls'], {'verify': False})

    def test_rule_units_alias_conditions_and_actions_are_normalized(self):
        parser = RuleParser(
            commands={'act': {'exec': ['restart', {'(id)': {'kwargs': {'user': 'nobody'}}}]}},
            conditions={'busy': {'expr': ['mem_usage > 1 KiB', 'status in (running,paused)']}})
        rule = parser.parse('@busy ? @act')
        self.assertTrue(rule.needs_metrics)
        self.assertEqual(rule.conditions[0].value, 1024)
        self.assertEqual([action.kind for action in rule.actions], ['docker', 'exec'])
        self.assertTrue(RuleEvaluator().matches(rule, ContainerSnapshot(status='running', mem_usage=2048)))
        self.assertFalse(RuleEvaluator().matches(rule, ContainerSnapshot(status='exited', mem_usage=2048)))
        rule.actions[1].kwargs['user'] = 'changed'
        self.assertEqual(parser.parse('@busy ? @act').actions[1].kwargs['user'], 'nobody')

    def test_comparisons_retain_numeric_and_membership_semantics(self):
        parser, evaluator = RuleParser(), RuleEvaluator()
        snapshot = ContainerSnapshot(cpu_percent=256, status='running', pid=123)
        for expression, expected in [('cpu_percent > 200 ? restart', True),
                                     ('cpu_percent < 200 ? restart', False),
                                     ('cpu_percent == 256 ? restart', True),
                                     ('cpu_percent != 256 ? restart', False),
                                     ('cpu_percent >= 256 ? restart', True),
                                     ('cpu_percent <= 256 ? restart', True),
                                     ('pid == 123 ? restart', True),
                                     ('status not in (paused,exited) ? restart', True)]:
            self.assertEqual(evaluator.matches(parser.parse(expression), snapshot), expected, expression)

    def test_historical_precondition_operand_order_is_preserved(self):
        rule = RuleParser().parse('300 > cpu_percent > 200 ? restart')
        self.assertFalse(RuleEvaluator().matches(rule, ContainerSnapshot(cpu_percent=256)))
        self.assertTrue(RuleEvaluator().matches(rule, ContainerSnapshot(cpu_percent=400)))

    def test_bad_syntax_and_unknown_alias_or_action_fail_during_parsing(self):
        parser = RuleParser()
        with self.assertRaises(RuleSyntaxError):
            parser.parse('?')
        for expression in ('@missing', '@missing ? restart', 'unknown_action'):
            with self.subTest(expression=expression), self.assertRaises(MonitoringError) as error:
                parser.parse(expression)
            self.assertEqual(error.exception.code, 110)


if __name__ == '__main__':
    unittest.main()
