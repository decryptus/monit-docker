"""Direct serve merges explicit CLI arguments, YAML and built-in defaults."""
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from monit_docker import cli
from monit_docker.adapters.validation import ConfigurationCheckError, check_configuration


class ServeConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = Path(directory.name) / 'monit-docker.yml'

    def parse(self, *args):
        return cli.argv_parse_check(['-c', str(self.config), 'serve', *args])

    def test_yaml_only_reaches_listener_scheduler_and_status_auth(self):
        secret = self.config.with_name('status-token')
        secret.write_text('a' * 64)
        self.config.write_text(f'serve: {{port: 9811, interval: 60, status_token_file: "{secret}"}}')
        with patch('monit_docker.adapters.http.run_server') as listen:
            self.assertEqual(cli.MonitDockerSubCmdServe(self.parse())(), None)
        monitor, bind, port = listen.call_args.args
        self.assertEqual((bind, port), ('127.0.0.1', 9811))
        self.assertEqual(monitor.interval, 60)
        self.assertEqual(listen.call_args.kwargs['security'].status_token, 'a' * 64)

    def test_cli_replaces_each_list_and_explicit_default_beats_yaml(self):
        self.config.write_text('serve: {port: 9011, interval: 60, rsc: [pid], cmd: [stop], state_file: state}')
        options = self.parse('--port', '9808', '--rsc', 'health', '--rsc', 'status',
                             '--cmd-if', 'restart', '--cmd', 'pause')
        self.assertEqual((options.port, options.interval, options.stale_after), (9808, 60, 180))
        self.assertEqual(options.resource, ['health', 'status'])
        self.assertEqual(options.cmd, ['restart', 'pause'])
        self.assertEqual(options.state_file, 'state')

    def test_cli_alone_and_inline_yaml_match_disk_config(self):
        with patch.object(cli, 'MONIT_DOCKER_CONFIG', None):
            self.assertEqual(self.parse('--interval', '45').interval, 45)
            self.assertEqual(self.parse().port, 9808)
        with patch.object(cli, 'MONIT_DOCKER_CONFIG', 'serve: {allow_maintenance: false, port: 9010}'):
            self.assertEqual(self.parse().port, 9010)
            self.config.write_text('serve: {port: 9012}')
            self.assertEqual(self.parse().port, 9012)

    def test_cross_field_validation_uses_effective_settings(self):
        self.config.write_text('serve: {allow_actions: true, action_origin: "https://test.local", state_file: state}')
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            self.parse()
        self.assertTrue(self.parse('--action-token-file', 'secret').allow_actions)
        self.config.write_text('serve: {status_token_file: status}')
        with patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit):
            self.parse('--status-token-file', '')

    def test_disable_configured_actions_from_cli(self):
        self.config.write_text(
            'serve: {allow_actions: true, action_origin: "https://test.local", action_token_file: secret, '
            'allow_maintenance: true, trust_proxy_user: true, state_file: state}')
        options = self.parse('--no-allow-actions')
        self.assertFalse(options.allow_actions)
        self.assertFalse(options.allow_maintenance)
        self.assertFalse(options.trust_proxy_user)
        self.assertIsNone(options.action_token_file)
        self.assertIsNone(options.action_origin)
        options = self.parse('--no-trust-proxy-user', '--no-allow-maintenance')
        self.assertTrue(options.allow_actions)
        self.assertFalse(options.trust_proxy_user)
        self.assertFalse(options.allow_maintenance)

    def test_check_config_validates_serve_rule_aliases(self):
        with self.assertRaises(ConfigurationCheckError) as error:
            check_configuration(str(self.config), 'serve: {cmd: ["@missing"], state_file: state}')
        self.assertEqual(error.exception.location, 'serve.cmd[0]')

    def test_offline_validation_rejects_invalid_types_options_and_inline_secrets(self):
        for entry in ('status_token: private', 'dry_run: true', 'port: true', 'allow_actions: "false"',
                      'status_token_file: ""', 'interval: .inf', 'rsc: [unknown]', 'cmd: restart'):
            with self.subTest(entry=entry), self.assertRaises(ConfigurationCheckError):
                check_configuration(str(self.config), 'serve: {' + entry + '}')
