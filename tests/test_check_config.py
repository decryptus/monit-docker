"""Offline validation must catch dormant errors without opening Docker or state."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monit_docker import cli
from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.validation import check_configuration


class CheckConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.yml'
        self.config.write_text('{}\n')
        self.addCleanup(patch.stopall)
        patch.object(cli, 'MONIT_DOCKER_CONFIG', None).start()
        self.docker = patch.object(cli.docker, 'DockerClient', side_effect=AssertionError('Docker connected')).start()
        self.env = patch.object(cli.docker, 'from_env', side_effect=AssertionError('Docker connected')).start()
        self.factory = patch.object(cli, 'client_factory', side_effect=AssertionError('Docker configured')).start()
        self.logs = patch.object(cli, 'WatchedFileHandler', side_effect=AssertionError('log opened')).start()
        self.runtime = patch.object(cli.helpers, 'make_dirs', side_effect=AssertionError('runtime created')).start()

    def invoke(self, options=(), rules=(), output='json'):
        argv = ['monit-docker', '-c', str(self.config), '--logfile', str(self.root / 'log'),
                '--runtimedir', str(self.root / 'run')] + list(options) + ['check-config', '--output', output]
        for rule in rules:
            argv += ['--cmd-if', rule]
        stream = io.StringIO()
        with patch.object(cli.sys, 'argv', argv), patch.object(cli.sys, 'stdout', stream):
            code = cli.main(cli.argv_parse_check())
        self.assertFalse((self.root / 'log').exists())
        self.assertFalse((self.root / 'run').exists())
        for mock in (self.docker, self.env, self.factory, self.logs, self.runtime):
            mock.assert_not_called()
        return code, json.loads(stream.getvalue()) if output == 'json' else stream.getvalue()

    def invalid(self, location=None, **kwargs):
        code, result = self.invoke(**kwargs)
        self.assertEqual(code, 110, result)
        self.assertFalse(result['valid'])
        self.assertEqual(len(result['errors']), 1)
        if location:
            self.assertIn(location, result['errors'][0]['location'])
        return result

    def test_empty_mapping_and_text_result(self):
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertTrue(result['valid'])
        self.assertEqual(result['summary'], dict(clients=0, groups=0, commands=0, conditions=0, rules=0))
        code, text = self.invoke(output='text')
        self.assertEqual(code, 0)
        self.assertIn('Configuration valid', text)

    def test_file_is_required_unlike_runtime_default(self):
        self.config.unlink()
        self.invalid(str(self.config))
        self.assertEqual(Configuration(str(self.config)).load(), {})

    def test_inline_fallback_and_file_precedence_match_runtime(self):
        with patch.object(cli, 'MONIT_DOCKER_CONFIG', 'commands: {bad: {exec: [unsupported]}}'):
            self.assertEqual(self.invoke()[0], 0)
            self.config.unlink()
            self.invalid('commands.bad.exec')
        with patch.object(cli, 'MONIT_DOCKER_CONFIG', '{}'):
            self.assertEqual(self.invoke()[0], 0)

    def test_relative_imports_templates_aliases_and_selection(self):
        (self.root / 'commands.yml').write_text('act:\n  exec:\n    - restart:\n        kwargs:\n          timeout: ${vars["seconds"]}\n')
        self.config.write_text('vars: {seconds: 7}\nclients: {local: {config: {base_url: "unix:///missing.sock"}}}\n'
                               'commands: {"@import_command": commands.yml}\n'
                               'conditions: {busy: {expr: ["mem_usage > 1 KiB", "cpu_percent > 60"]}}\n'
                               'ctn-groups: {web: {match: ["name:web*"]}}\n')
        code, result = self.invoke(options=['--client', 'local', '--ctn-group', 'web'], rules=['@busy ? @act'])
        self.assertEqual(code, 0, result)
        self.assertEqual(result['summary'], dict(clients=1, groups=1, commands=1, conditions=1, rules=1))

    def test_missing_import_and_yaml_error_have_locations(self):
        self.config.write_text('commands: {"@import_command": missing.yml}\n')
        self.invalid('missing.yml')
        (self.root / 'missing.yml').write_text('broken: [\n')
        error = self.invalid('missing.yml')['errors'][0]
        self.assertIn('line', error['message'])
        self.config.write_text('commands: [\n')
        self.invalid(str(self.config))

    def test_bad_import_structure_and_template_failure(self):
        for content in ('commands: {"@import_command": 42}', 'commands: {"@import_wrong": x.yml}',
                        'commands: {a: {exec: ["${missing_variable}"]}}'):
            with self.subTest(content=content):
                self.config.write_text(content)
                self.invalid()
        (self.root / 'import.yml').write_text('[]')
        self.config.write_text('commands: {"@import_command": import.yml}')
        self.invalid('import.yml')

    def test_shapes_and_dormant_aliases_are_checked(self):
        examples = ['[]', '', 'null', 'unknown: {}', 'vars: []', 'commands: []',
                    'commands: {a: null}', 'commands: {a: {exec: restart}}',
                    'commands: {a: {exec: []}}', 'commands: {a: {exec: [unsupported]}}',
                    'commands: {a: {exec: [{restart: {}, stop: {}}]}}',
                    'commands: {a: {exec: [{restart: {args: wrong}}]}}',
                    'commands: {a: {exec: [{restart: {kwargs: []}}]}}',
                    'conditions: {a: {expr: ["unknown_resource > 1"]}}',
                    'conditions: {a: {expr: ["cpu_percent > 1", "pid > 1.5"]}}',
                    'conditions: {a: {expr: ["mem_usage > 1.5"]}}',
                    'clients: {a: {config: []}}', 'clients: {a: {config: {tls: 42}}}']
        for content in examples:
            with self.subTest(content=content):
                self.config.write_text(content)
                self.invalid()

    def test_invalid_group_and_cli_regex_are_checked(self):
        self.config.write_text('ctn-groups: {bad: {match: ["name:~["]}}')
        self.invalid('ctn-groups.bad.match')
        self.config.write_text('{}')
        self.invalid('selectors', options=['--name', '~['])
        self.invalid('selectors', options=['--ctn-group', 'missing'])

    def test_unknown_client_and_explicit_environment_precedence(self):
        self.invalid('--client', options=['--client', 'missing'])
        self.assertEqual(self.invoke(options=['--client', 'ignored', '--client-from-env'])[0], 0)

    def test_additional_rules_are_all_checked_without_execution(self):
        self.invalid('--cmd-if[1]', rules=['restart', '@missing'])
        self.assertEqual(self.invoke(rules=['(touch /tmp/should-not-run)', 'mem_percent > 90 ? restart'])[0], 0)

    def test_diagnostics_do_not_dump_secret_values(self):
        secret = 'private-token-do-not-print'
        self.config.write_text('vars: {secret: "%s"}\ncommands: {a: {exec: ["${vars[\'secret\']}"]}}' % secret)
        result = self.invalid('commands.a.exec')
        self.assertNotIn(secret, json.dumps(result))

    def test_example_configuration_when_available(self):
        sample = Path(__file__).resolve().parents[1] / 'etc/monit-docker/monit-docker.yml.example'
        if not sample.exists():
            self.skipTest('requires example files from source checkout')
        result = check_configuration(str(sample))
        self.assertEqual(result['commands'], 3)
        self.assertEqual(result['conditions'], 6)


if __name__ == '__main__':
    unittest.main()
