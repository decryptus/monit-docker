import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from httpdis.httpdis import crypt
from monit_docker.adapters.auth_migration import migrate, rollback
from monit_docker.adapters.local_auth import Accounts
from test_local_auth import PASSWORD

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = self.root / 'htpasswd'
        self.roles = self.root / 'roles.json'
        self.database = self.root / 'auth.db'
        self.backup = self.root / 'backup'
        self.source.write_text('alice:' + crypt(PASSWORD, '$6$fixture$') + '\n')
        self.roles.write_text('{"alice":"viewer"}')
        self.original = self.source.read_bytes()

    def apply(self):
        return migrate(self.source, self.roles, self.database, self.backup, apply=True)

    def test_preview_performs_no_writes(self):
        result = migrate(self.source, self.roles, self.database)
        self.assertFalse(result['applied'])
        self.assertFalse(self.database.exists())
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_import_preserves_password_and_upgrades_verified_hash(self):
        self.assertTrue(self.apply()['applied'])
        accounts = Accounts(self.database)
        self.addCleanup(accounts.close)
        grant = accounts.login('alice', PASSWORD, 'fixture')
        self.assertEqual(grant.identity.scopes, {'status:read'})
        with accounts.store.transaction() as tx:
            self.assertTrue(tx.get('accounts', 'alice')['hash'].startswith('$argon2id$'))
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual((self.backup / 'htpasswd.original').read_bytes(), self.original)
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o700)

    def test_all_rights_must_be_explicit(self):
        for roles in ({}, {'alice': 'unknown'}, {'alice': 'viewer', 'bob': 'admin'}):
            self.roles.write_text(json.dumps(roles))
            with self.assertRaises(ValueError):
                self.apply()
            self.assertFalse(self.database.exists())
            self.assertFalse(self.backup.exists())

    def test_duplicate_accounts_and_roles_rejected(self):
        self.source.write_bytes(self.original + self.original)
        with self.assertRaises(ValueError):
            self.apply()
        self.source.write_bytes(self.original)
        self.roles.write_text('{"alice":"viewer","alice":"admin"}')
        with self.assertRaises(ValueError):
            self.apply()

    def test_unsupported_hash_never_silently_skipped(self):
        for encoded in ('{SHA}old', 'plaintext', '$apr1$unsupported'):
            self.source.write_text('alice:' + encoded + '\n')
            with self.assertRaises(ValueError):
                self.apply()
            self.assertFalse(self.database.exists())

    def test_existing_database_is_never_overwritten(self):
        self.database.write_bytes(b'keep-existing-data')
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.database.read_bytes(), b'keep-existing-data')

    def test_symlink_source_is_rejected(self):
        self.source.rename(self.root / 'original')
        self.source.symlink_to(self.root / 'original')
        with self.assertRaises(OSError):
            self.apply()

    def test_rollback_preserves_database_and_requires_stopped_writers(self):
        self.apply()
        original_db = self.database.read_bytes()
        with self.assertRaises(ValueError):
            rollback(self.database, self.backup)
        result = rollback(self.database, self.backup, writers_stopped=True)
        self.assertFalse(self.database.exists())
        self.assertEqual(Path(result['retained_database']).read_bytes(), original_db)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_damaged_backup_prevents_rollback(self):
        self.apply()
        (self.backup / 'htpasswd.original').write_text('changed')
        with self.assertRaises(ValueError):
            rollback(self.database, self.backup, writers_stopped=True)
        self.assertTrue(self.database.exists())

    def test_interrupted_import_never_publishes_partial_database(self):
        with patch('monit_docker.adapters.auth_migration.Accounts.list_users', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertFalse(self.database.exists())
        self.assertEqual(list(self.root.glob('.auth-migration-*')), [])
        self.assertEqual((self.backup / 'htpasswd.original').read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_concurrent_input_change_aborts_before_publication(self):
        from monit_docker.adapters.auth_migration import read_file
        calls = []
        def changing(path, *args):
            if Path(path) == self.source:
                calls.append(path)
                if len(calls) == 2:
                    return self.original + b'# concurrent edit\n'
            return read_file(path, *args)
        with patch('monit_docker.adapters.auth_migration.read_file', side_effect=changing):
            with self.assertRaises(ValueError):
                self.apply()
        self.assertFalse(self.database.exists())
        self.assertEqual((self.backup / 'htpasswd.original').read_bytes(), self.original)

    def test_backup_sync_failure_prevents_publication(self):
        with patch('monit_docker.adapters.auth_migration.sync_directory', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertFalse(self.database.exists())
        self.assertEqual(self.source.read_bytes(), self.original)
