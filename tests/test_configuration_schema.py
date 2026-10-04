"""XYS shares the existing offline/runtime configuration contract."""
import unittest
from unittest.mock import patch
from monit_docker.adapters.validation import CheckedConfiguration, check_configuration
from monit_docker.adapters.validation_rules import ConfigurationCheckError, mapping, string_list


class ConfigurationSchemaTests(unittest.TestCase):
    def test_mapping_key_and_string_list_contracts(self):
        mapping({'x': {'opaque': [1]}}, 'fixture')
        string_list(['name:web,worker', 'name:db'], 'fixture')
        for value in ([], {1: 'PRIVATE'}):
            with self.assertRaises(ConfigurationCheckError):
                mapping(value, 'fixture')
        for value in ('a,b', [], [False], ['  '], [1]):
            with self.assertRaises(ConfigurationCheckError):
                string_list(value, 'fixture')

    def test_root_and_unused_rules_keep_existing_behavior(self):
        for content in ('general: []', 'clients: []', 'unknown: {}'):
            with self.assertRaises(ConfigurationCheckError):
                CheckedConfiguration('/nonexistent', content).load()
        conf = CheckedConfiguration('/nonexistent', 'commands: {broken: {}}')
        self.assertEqual(conf.load(include_rules=False), {})
        with self.assertRaises(ConfigurationCheckError):
            conf.load()

    def test_mappings_are_validated_in_runtime_loader(self):
        with patch('monit_docker.adapters.validation.xys.validate', return_value=False):
            with self.assertRaises(ConfigurationCheckError):
                CheckedConfiguration('/nonexistent', 'clients: {}').load()

    def test_comma_is_literal_and_aliases_still_work(self):
        content = 'ctn-groups: {web: {match: ["name:web,worker"]}}'
        loaded = CheckedConfiguration('/nonexistent', content).load()
        self.assertEqual(loaded['ctn-groups']['web']['match'], ['name:web,worker'])
        self.assertEqual(check_configuration('/nonexistent', content)['groups'], 1)
