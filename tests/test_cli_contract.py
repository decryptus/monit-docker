"""Observable configuration and target-selection behavior for the 1.0 inventory."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from monit_docker import cli
from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.docker import client_factory, DockerCollector
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.validation import CheckedConfiguration
from test_monit_docker import container


class CliContractTests(unittest.TestCase):
    def test_selectors_are_alternatives_and_status_is_an_additional_filter(self):
        selector = ContainerSelector(selectors={'name': ['web-*, api-*'], 'label': ['frontend']},
                                     statuses=['running'])
        for name, status, labels, expected in (
                ('web-one', 'running', (), True), ('api-one', 'running', (), True),
                ('db-one', 'running', ('frontend',), True),
                ('web-one', 'exited', ('frontend',), False), ('db-one', 'running', (), False)):
            with self.subTest(name=name, status=status, labels=labels):
                self.assertEqual(selector.matches('a' * 64, name, status, labels, ()), expected)

    def test_regex_matches_from_start_while_glob_matches_the_whole_value(self):
        for pattern, name, expected in (('web', 'web-one', False), ('web*', 'web-one', True),
                                        ('~web', 'web-one', True), ('~web$', 'web-one', False),
                                        ('~web', 'my-web', False), ('~.*web', 'my-web', True)):
            with self.subTest(pattern=pattern, name=name):
                selector = ContainerSelector(selectors={'name': [pattern]})
                self.assertEqual(selector.matches('a' * 64, name, 'running', (), ()), expected)

    def test_group_patterns_preserve_regex_commas_and_replace_direct_selectors(self):
        selector = ContainerSelector(selectors={'name': ['db*']}, statuses=['running'],
                                     groups={'web': {'match': ['name:~web[0-9]{1,3}$']}},
                                     selected_groups=['web'])
        self.assertTrue(selector.matches('a' * 64, 'web123', 'running', (), ()))
        self.assertFalse(selector.matches('a' * 64, 'web1234', 'running', (), ()))
        self.assertFalse(selector.matches('a' * 64, 'db-one', 'running', (), ()))
        self.assertFalse(selector.matches('a' * 64, 'web12', 'exited', (), ()))

    def test_docker_labels_match_values_and_image_selectors_match_tags(self):
        obj = container('app')
        obj.labels = {'role': 'frontend'}
        obj.image.tags = ['example/app:stable']
        client = Mock()
        client.containers.list.return_value = [obj]
        for selectors, expected in (({'label': ['role']}, False),
                                     ({'label': ['frontend']}, True),
                                     ({'label': ['role=frontend']}, False),
                                     ({'image': ['example/app:*']}, True)):
            with self.subTest(selectors=selectors):
                collector = DockerCollector(lambda: client, ContainerSelector(selectors=selectors))
                collector.begin_cycle()
                try:
                    self.assertEqual(bool(collector.select()), expected)
                finally:
                    collector.end_cycle()

    def test_later_imports_override_earlier_and_local_entries_replace_whole_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'first.yml').write_text('act: {exec: [pause]}\nother: {exec: [stop]}\n')
            (root / 'second.yml').write_text('act: {exec: [restart]}\n')
            config = root / 'config.yml'
            config.write_text('commands:\n  "@import_command": [first.yml, second.yml]\n')
            for loader in (Configuration, CheckedConfiguration):
                self.assertEqual(loader(str(config)).load()['commands']['act']['exec'], ['restart'])
            config.write_text(config.read_text() + '  act: {exec: [start]}\n')
            for loader in (Configuration, CheckedConfiguration):
                result = loader(str(config)).load()['commands']
                self.assertEqual(result['act']['exec'], ['start'])
                self.assertEqual(result['other']['exec'], ['stop'])

    def test_client_selection_uses_first_entry_unless_explicitly_overridden(self):
        config = {'clients': {'second': {'config': {'base_url': 'unix:///second.sock'}},
                              'first': {'config': {'base_url': 'unix:///first.sock'}}}}
        with patch('monit_docker.adapters.docker.docker.DockerClient') as configured, \
                patch('monit_docker.adapters.docker.docker.from_env') as environment:
            client_factory(config)()
            configured.assert_called_with(base_url='unix:///second.sock')
            client_factory(config, 'first')()
            configured.assert_called_with(base_url='unix:///first.sock')
            configured.reset_mock()
            client_factory(config, 'missing', from_env=True)()
            environment.assert_called_once_with()
            configured.assert_not_called()

    def test_global_options_followed_by_subcommand_options_and_invalid_placement(self):
        options = cli.argv_parse_check(['--name', 'web-*', 'stats', '--rsc', 'status'])
        self.assertEqual(options.name, ['web-*'])
        self.assertEqual(options.resource, ['status'])
        for arguments in (['stats', '--name', 'web-*'], ['--rsc', 'status', 'stats'],
                          ['monit', '--rsc', 'status', '--cmd', 'restart']):
            with self.subTest(arguments=arguments), patch.object(cli.sys, 'stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.argv_parse_check(arguments)
                self.assertEqual(error.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
