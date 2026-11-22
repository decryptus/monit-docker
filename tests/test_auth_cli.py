import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monit_docker.cli import argv_parse_check, main

class AuthCliTests(unittest.TestCase):
    def test_offline_provisioning_and_listing_do_not_touch_docker(self):
        with tempfile.TemporaryDirectory() as root:
            password = Path(root) / 'password'
            password.write_text('test-only-password-123\n')
            password.chmod(0o600)
            database = str(Path(root) / 'auth.db')
            output = io.StringIO()
            with patch('monit_docker.cli.initialize_runtime', side_effect=AssertionError('runtime started')), contextlib.redirect_stdout(output):
                code = main(argv_parse_check(['auth', 'user-add', '--database', database, '--username', 'reader@example.com',
                    '--role', 'viewer', '--password-file', str(password)]))
                self.assertEqual(code, 0)
                self.assertEqual(main(argv_parse_check(['auth', 'users', '--database', database])), 0)
            self.assertIn('viewer', output.getvalue())
            self.assertNotIn('test-only-password', output.getvalue())

    def test_sqlite_mode_requires_origin_and_disallows_legacy_secrets(self):
        for args in (['--auth-db', '/tmp/db'],
                     ['--auth-origin', 'https://monitor.example'],
                     ['--auth-db', '/tmp/db', '--auth-origin', 'https://monitor.example', '--status-token-file', '/tmp/token']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                argv_parse_check(['serve'] + args)

    def test_sqlite_actions_do_not_require_proxy_secret(self):
        options = argv_parse_check(['serve', '--auth-db', '/tmp/db', '--auth-origin', 'https://monitor.example',
                                    '--allow-actions', '--state-file', '/tmp/state'])
        self.assertTrue(options.allow_actions)
        self.assertIsNone(options.action_token_file)

    def test_migration_defaults_to_preview_and_requires_roles(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            argv_parse_check(['auth', 'migrate', '--database', '/tmp/db', '--htpasswd', '/tmp/users'])
        options = argv_parse_check(['auth', 'migrate', '--database', '/tmp/db', '--htpasswd', '/tmp/users', '--roles-file', '/tmp/roles'])
        self.assertFalse(options.apply)

    def test_yaml_supports_authentication_settings(self):
        from monit_docker.adapters.command_config import validate_command_section
        validate_command_section('serve', {'auth_db': '/private/auth.db', 'auth_origin': 'https://monitor.example', 'auth_audit': True})
