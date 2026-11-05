"""Effective settings of direct commands, including observation versus action."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monit_docker import cli
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError


class DirectCommandConfigTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'monit-docker.yml'

    def parse(self, *args):
        return cli.argv_parse_check(['-c', str(self.config), *args])

    def test_stats_resource_list_and_default_value_override_yaml(self):
        self.config.write_text('stats: {rsc: [pid], output: text}\nmonit: {cmd: [stop]}')
        self.assertEqual(self.parse('stats').resource, ['pid'])
        options = self.parse('stats', '--output', 'json', '--rsc', 'health', '--rsc', 'status')
        self.assertEqual((options.resource, options.output), (['health', 'status'], 'json'))
        self.assertFalse(cli.job_options(options).cmd)

    def test_monit_explicit_observation_never_executes_configured_action(self):
        self.config.write_text('monit: {cmd: [stop], propagate_exit_code: true}')
        options = self.parse('monit', '--rsc', 'status')
        self.assertEqual((options.resource, options.cmd, options.propagate_exit_code), (['status'], [], False))
        self.config.write_text('monit: {rsc: [pid]}')
        options = self.parse('monit', '--cmd-if', 'restart', '--dry-run')
        self.assertEqual((options.cmd, options.resource, options.dry_run), (['restart'], [], True))
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            self.parse('monit', '--rsc', 'pid', '--cmd', 'restart')

    def test_cron_combines_cli_and_yaml_before_validation(self):
        self.config.write_text('cron: {state_file: persisted.json, cmd: [restart], cooldown: 27, '
                               'trigger_after: 120, max_gap: 90, max_restarts: 2, propagate_exit_code: true}')
        configured = self.parse('cron')
        self.assertEqual(configured.cmd, ['restart'])
        self.assertTrue(configured.propagate_exit_code)
        options = self.parse('cron', '--cmd-if', 'pause', '--cooldown', '300', '--no-propagate-exit-code')
        self.assertEqual((options.state_file, options.cmd, options.cooldown), ('persisted.json', ['pause'], 300))
        self.assertEqual((options.trigger_after, options.max_gap, options.max_restarts), (120, 90, 2))
        self.assertFalse(options.propagate_exit_code)
        self.config.write_text('cron: {cooldown: 15}')
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            self.parse('cron', '--cmd', 'pause')
        self.assertEqual(self.parse('cron', '--cmd', 'pause', '--state-file', 'state').cooldown, 15)

    def test_offline_check_rejects_mistyped_fields_and_invalid_rules(self):
        for section in ('stats: {output: xml}', 'cron: {rsc: [pid]}', 'monit: {propagate_exit_code: "false"}',
                        'cron: {cmd: ["@missing"]}', 'monit: {rsc: [pid], cmd: [stop]}'):
            with self.subTest(section=section), self.assertRaises(ConfigurationCheckError):
                self.config.write_text(section)
                check_configuration(str(self.config))
