"""Offline migration: preserve originals, verify output and expose interruptions."""
from contextlib import redirect_stderr, redirect_stdout
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from monit_docker.audit import AuditError, AuditJournal, MAX_RECORD_BYTES, prepare_record
from monit_docker.audit_migrate import migrate_journal
from monit_docker import audit_migrate
import test_monit_docker as legacy_tests


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'events.jsonl'
        self.archive = self.root / 'events.jsonl.1'
        self.output = self.root / 'migration'
        self.journal = AuditJournal(self.path, max_bytes=65536, files=3, emit=False)
        legacy = dict(schema_version=1, event_id='one', correlation_id='request-one',
                      actor='=operator\n', reason=r'literal\u000a', extra='café 🐳', exit_code=0)
        current = dict(schema_version=2, event_id='two', correlation_id='request-one',
                       actor=r'\u0040operator', reason='execution_completed', extra=None)
        self.archive.write_text(json.dumps(legacy) + '\n', encoding='utf-8')
        self.path.write_text(json.dumps(current) + '\n', encoding='utf-8')
        self.originals = {p.name: p.read_bytes() for p in (self.archive, self.path)}
        self.expected = [prepare_record(legacy), current]

    def assert_sources_unchanged(self):
        self.assertEqual({name: (self.root / name).read_bytes() for name in self.originals}, self.originals)

    def test_dry_run_is_default_and_creates_no_bundle_or_data_changes(self):
        report = migrate_journal(self.journal, self.output)
        self.assertEqual((report['status'], report['records'], report['legacy_records']), ('dry_run', 2, 1))
        self.assertFalse(self.output.exists())
        self.assert_sources_unchanged()

    def test_apply_preserves_backups_layout_identity_and_permissions(self):
        report = migrate_journal(self.journal, self.output, apply=True)
        self.assertEqual(report['status'], 'complete')
        self.assertEqual(json.loads((self.output / 'manifest.json').read_text()), report)
        for item in report['files']:
            name = item['name']
            self.assertEqual((self.output / 'backup' / name).read_bytes(), self.originals[name])
            self.assertEqual(item['source_sha256'], hashlib.sha256(self.originals[name]).hexdigest())
            self.assertEqual(item['converted_sha256'], hashlib.sha256((self.output / 'converted' / name).read_bytes()).hexdigest())
        result = AuditJournal(self.output / 'converted/events.jsonl', files=3, emit=False).read()
        self.assertEqual(result, self.expected)
        for path in (self.output, *self.output.rglob('*')):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assert_sources_unchanged()

    def test_second_conversion_is_idempotent_and_existing_destinations_are_refused(self):
        migrate_journal(self.journal, self.output, apply=True)
        second = self.root / 'second'
        migrated = AuditJournal(self.output / 'converted/events.jsonl', files=3, emit=False)
        report = migrate_journal(migrated, second, apply=True)
        self.assertEqual(report['legacy_records'], 0)
        for name in self.originals:
            self.assertEqual((second / 'converted' / name).read_bytes(), (self.output / 'converted' / name).read_bytes())
        for target in (self.output, self.path):
            with self.assertRaises(AuditError):
                migrate_journal(self.journal, target, apply=True)
        self.assert_sources_unchanged()

    def test_invalid_input_fails_before_creating_destination(self):
        records = [b'{invalid}\n', b'{"schema_version":1}', b'\xff\n',
                   b'{"schema_version":3}\n', b'{"schema_version":true}\n',
                   b'{"schema_version":1,"actor":"a","actor":"b"}\n',
                   b'{"schema_version":1,"exit_code":NaN}\n',
                   b'{"schema_version":1,"duration_ms":1e999}\n',
                   b'{"schema_version":2,"actor":"=formula"}\n',
                   b'{"schema_version":1,"extra":{}}\n',
                   b'x' * MAX_RECORD_BYTES + b'\n',
                   (json.dumps(dict(schema_version=1, actor='\\' * 20000)) + '\n').encode()]
        for data in records:
            with self.subTest(data=data[:80]):
                self.archive.write_bytes(data)
                with self.assertRaises(AuditError):
                    migrate_journal(self.journal, self.output, apply=True)
                self.assertFalse(self.output.exists())
                self.assertEqual(self.archive.read_bytes(), data)

    def test_retention_must_include_all_archives_and_fit_converted_bytes(self):
        extra = self.root / 'events.jsonl.3'
        extra.write_bytes(self.originals[self.archive.name])
        with self.assertRaisesRegex(AuditError, 'audit-files'):
            migrate_journal(self.journal)
        extra.unlink()
        row = (json.dumps(dict(schema_version=1, actor='\\' * 15000)) + '\n').encode()
        self.archive.write_bytes(row * 3)  # source fits; normalization expands beyond configured bound
        with self.assertRaisesRegex(AuditError, 'retention size'):
            migrate_journal(self.journal, self.output, apply=True)
        self.assertFalse(self.output.exists())
        larger = AuditJournal(self.path, max_bytes=200000, files=3, emit=False)
        self.assertEqual(migrate_journal(larger)['records'], 4)

    def test_missing_and_empty_sources_are_distinguished(self):
        self.path.unlink()
        self.archive.unlink()
        with self.assertRaisesRegex(AuditError, 'No retained'):
            migrate_journal(self.journal, self.output, apply=True)
        self.path.touch()
        report = migrate_journal(self.journal, self.output, apply=True)
        self.assertEqual(report['records'], 0)
        self.assertEqual((self.output / 'converted/events.jsonl').read_bytes(), b'')

    def test_destination_cannot_occupy_a_missing_rotation_or_lock_path(self):
        for suffix in ('.2', '.99', '.lock'):
            with self.subTest(suffix=suffix):
                destination = Path(str(self.path) + suffix)
                with self.assertRaisesRegex(AuditError, 'conflicts'):
                    migrate_journal(self.journal, destination, apply=True)
                self.assertFalse(destination.exists())
        self.assert_sources_unchanged()

    def test_symlinks_and_special_files_are_rejected_without_following(self):
        outside = self.root / 'outside'
        outside.write_text('preserve')
        for path in (self.archive, Path(str(self.path) + '.lock'), self.output):
            with self.subTest(path=path.name):
                if path.exists():
                    path.unlink()
                path.symlink_to(outside)
                with self.assertRaises(AuditError):
                    migrate_journal(self.journal, self.output, apply=True)
                self.assertEqual(outside.read_text(), 'preserve')
                path.unlink()
                if path == self.archive:
                    path.write_bytes(self.originals[path.name])
        self.archive.unlink()
        os.mkfifo(self.archive)
        with self.assertRaises(AuditError):
            migrate_journal(self.journal, self.output, apply=True)

    def test_busy_writer_is_reported_without_waiting(self):
        with open(str(self.path) + '.lock', 'wb') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(AuditError, 'busy'):
                migrate_journal(self.journal, self.output, apply=True)
        self.assertFalse(self.output.exists())
        self.assert_sources_unchanged()

    def test_write_failure_leaves_no_completion_marker_and_does_not_overwrite_on_retry(self):
        with patch('monit_docker.audit_migrate.os.fsync', side_effect=OSError('disk full')):
            with self.assertRaises(AuditError):
                migrate_journal(self.journal, self.output, apply=True)
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / 'manifest.json').exists())
        with self.assertRaises(AuditError):
            migrate_journal(self.journal, self.output, apply=True)
        self.assert_sources_unchanged()

    def test_changed_source_and_corrupted_conversion_do_not_receive_a_manifest(self):
        inspect = audit_migrate._inspect
        for corrupted in ('source', 'converted'):
            destination = self.root / corrupted
            def interfere(journal, path, backup=None, converted=None):
                if corrupted == 'source' and backup is not None:
                    path.write_bytes(path.read_bytes().replace(b'one', b'bad'))
                if corrupted == 'converted' and path.parent.name == 'converted':
                    path.write_bytes(path.read_bytes().replace(b'one', b'bad'))
                return inspect(journal, path, backup, converted)
            with self.subTest(corrupted=corrupted), patch.object(audit_migrate, '_inspect', side_effect=interfere):
                with self.assertRaises(AuditError):
                    migrate_journal(self.journal, destination, apply=True)
            self.assertFalse((destination / 'manifest.json').exists())
            for name, data in self.originals.items():
                (self.root / name).write_bytes(data)


class MigrationCliTests(unittest.TestCase):
    setUp = legacy_tests.RegressionTests.setUp
    invoke = legacy_tests.RegressionTests.invoke

    def test_offline_modes_errors_and_json_report(self):
        path = Path(self.temp.name) / 'events.jsonl'
        path.write_text('{"schema_version":1,"event_id":"one"}\n')
        destination = Path(self.temp.name) / 'converted'
        for mode in ([], ['--dry-run'], ['--apply', '--output-dir', str(destination)]):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(self.invoke('--audit-file', str(path), 'audit-migrate', *mode), 0)
            self.assertEqual(json.loads(output.getvalue())['records'], 1)
        for mode in (['--apply'], ['--apply', '--dry-run'], ['--output-dir', ' ']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.invoke('--audit-file', str(path), 'audit-migrate', *mode)
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke('audit-migrate')
        path.write_text('{"schema_version":3}\n')
        stderr, stdout = io.StringIO(), io.StringIO()
        with redirect_stderr(stderr), redirect_stdout(stdout):
            self.assertEqual(self.invoke('--audit-file', str(path), 'audit-migrate'), 119)
        self.assertIn('Unsupported audit schema', stderr.getvalue())
        self.assertEqual(stdout.getvalue(), '')
        self.client.containers.list.assert_not_called()
