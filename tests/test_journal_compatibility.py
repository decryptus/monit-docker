"""Retained schemas across rotation, pagination and administrative exports.

Fixtures are synthetic wire records, independent of the current event writer.
Schema 1 is the historical raw-text format; published 0.0.65 already writes v2.
"""
from contextlib import redirect_stderr, redirect_stdout
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from urllib.parse import urlencode

from monit_docker.audit import AuditError, AuditJournal, export_events
from monit_docker.audit_query import AuditReader, QueryError
from monit_docker.cli import main


class JournalCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'events.jsonl'
        self.archive = Path(str(self.path) + '.1')
        self.journal = AuditJournal(self.path, files=2, emit=False)
        self.reader = AuditReader(self.journal, 'd' * 64)

    @staticmethod
    def fixture(number, version):
        return dict(schema_version=version, event_id='%032x' % number,
                    timestamp='2026-09-01T00:00:00.000Z', host='host-one',
                    category='action', event='completed', source='manual',
                    correlation_id='request-one', result='succeeded',
                    actor='=operator\n' if version == 1 else r'\u003doperator\u000a',
                    reason='execution_completed', exit_code=0, duration_ms=12,
                    container_name='café 🐳', future_field='@extra' if version == 1 else r'\u0040extra')

    def write_records(self, path, records):
        path.write_bytes(b''.join((json.dumps(row, ensure_ascii=True) + '\n').encode()
                                 for row in records))

    def populate(self):
        self.write_records(self.archive, [self.fixture(n, 1) for n in range(60)])
        self.write_records(self.path, [self.fixture(n, 2) for n in range(60, 105)])
        return {path: path.read_bytes() for path in (self.archive, self.path)}

    def assert_unchanged(self, before):
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_mixed_rotations_paginate_and_export_without_rewriting(self):
        before = self.populate()
        expected = [self.fixture(n, 2) for n in range(105)]
        self.assertEqual(self.journal.read(), expected)
        first, _ = self.reader.page('correlation_id=request-one', 'operator')
        self.assertEqual(first['records'], expected[::-1][:100])
        second, _ = self.reader.page(urlencode(dict(correlation_id='request-one',
                                                   cursor=first['next_cursor'])), 'operator')
        self.assertEqual(second['records'], expected[::-1][100:])
        self.assertIsNone(second['next_cursor'])
        for format in ('jsonl', 'csv'):
            page, actual_format = self.reader.page(urlencode(dict(correlation_id='request-one',
                cursor=first['page_cursor'], format=format)), 'operator')
            self.assertEqual(actual_format, format)
            output = io.StringIO()
            export_events(page['records'], output, format)
            rows = ([json.loads(line) for line in output.getvalue().splitlines()] if format == 'jsonl'
                    else list(csv.DictReader(io.StringIO(output.getvalue()))))
            self.assertEqual([row['event_id'] for row in rows], [row['event_id'] for row in expected[::-1][:100]])
            self.assertTrue(all(row['actor'] == r'\u003doperator\u000a' for row in rows))
        self.assert_unchanged(before)

    def test_jsonl_round_trip_preserves_extension_fields_and_csv_is_a_projection(self):
        before = self.populate()
        output = io.StringIO()
        export_events(self.journal.read(), output)
        exported = output.getvalue()
        copy = self.path.parent / 'export.jsonl'
        copy.write_text(exported, encoding='utf-8')
        restored = AuditJournal(copy, files=1, emit=False).read()
        again = io.StringIO()
        export_events(restored, again)
        self.assertEqual(again.getvalue(), exported)
        self.assertTrue(all(row['future_field'] == r'\u0040extra' for row in restored))
        csv_output = io.StringIO()
        export_events(restored, csv_output, 'csv')
        row = next(csv.DictReader(io.StringIO(csv_output.getvalue())))
        self.assertNotIn('future_field', row)
        self.assertEqual(row['exit_code'], '0')
        self.assertEqual(row['error_code'], '')
        self.assertNotIn('error_code', restored[0])
        self.assert_unchanged(before)

    def test_cli_exports_both_schemas_before_applying_category_filters(self):
        before = self.populate()
        options = SimpleNamespace(subcommand='audit-export', audit_file=str(self.path),
                                  audit_files=2, category='action', since=None, format='jsonl')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(options), 0)
        self.assertEqual([json.loads(line) for line in output.getvalue().splitlines()],
                         [self.fixture(n, 2) for n in range(105)])
        self.assert_unchanged(before)

    def test_bad_old_records_fail_full_reads_and_scanned_pages_even_when_filtered_out(self):
        bad_records = [dict(self.fixture(0, 2), schema_version=value) for value in (3, True, 1.0)]
        bad_records += [dict(self.fixture(0, 2), actor='unescaped\n'),
                        dict(self.fixture(0, 1), future_field=['nested'])]
        bad_bytes = [(json.dumps(row) + '\n').encode() for row in bad_records]
        bad_bytes += [b'{invalid}\n', b'{"schema_version":1', b'\xff\n']
        for invalid in bad_bytes:
            with self.subTest(invalid=invalid):
                self.archive.write_bytes(invalid)
                self.write_records(self.path, [self.fixture(1, 2)])
                before = {path: path.read_bytes() for path in (self.archive, self.path)}
                with self.assertRaises(AuditError):
                    self.journal.read()
                with self.assertRaises(QueryError) as error:
                    self.reader.page('category=notification', 'operator')
                self.assertEqual((error.exception.code, error.exception.reason), (503, 'audit_unavailable'))
                for format in ('jsonl', 'csv'):
                    options = SimpleNamespace(subcommand='audit-export', audit_file=str(self.path),
                        audit_files=2, category='notification', since=None, format=format)
                    output = io.StringIO()
                    with redirect_stdout(output), redirect_stderr(io.StringIO()):
                        self.assertEqual(main(options), 119)
                    self.assertEqual(output.getvalue(), '')
                self.assert_unchanged(before)

    def test_a_successful_recent_page_does_not_certify_unscanned_archives(self):
        self.write_records(self.archive, [dict(self.fixture(0, 2), schema_version=3)])
        self.write_records(self.path, [self.fixture(n, 2) for n in range(1, 102)])
        first, _ = self.reader.page('', 'operator')
        self.assertEqual(len(first['records']), 100)
        self.assertIsNotNone(first['next_cursor'])
        with self.assertRaises(QueryError) as error:
            self.reader.page(urlencode(dict(cursor=first['next_cursor'])), 'operator')
        self.assertEqual(error.exception.code, 503)
