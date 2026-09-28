"""Bounded full exports preserve ordering, validation and snapshot lifetime."""
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from monit_docker.audit import AuditError, AuditJournal, encoded, event_record, export_events


class StreamingAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.jsonl'
        self.journal = AuditJournal(self.path, max_bytes=65536, files=3, emit=False)

    def test_jsonl_csv_match_materialized_export_with_mixed_schemas(self):
        self.path.with_name('events.jsonl.1').write_text(
            '{"schema_version":1,"event_id":"legacy","actor":"line\\nbreak"}\n')
        self.journal.append(event_record('action', 'completed', actor='é'))
        expected = self.journal.read()
        for format in ('jsonl', 'csv'):
            old, new = io.StringIO(), io.StringIO()
            export_events(expected, old, format)
            with self.journal.iter_records() as rows:
                export_events(rows, new, format)
            self.assertEqual(new.getvalue(), old.getvalue())

    def test_later_corruption_is_rejected_before_any_output(self):
        self.path.write_bytes(encoded(event_record('action', 'completed')) + b'{broken}\n')
        out = io.StringIO()
        with self.assertRaises(AuditError):
            with self.journal.iter_records() as rows:
                export_events(rows, out)
        self.assertEqual(out.getvalue(), '')

    def test_append_and_rotation_do_not_change_open_snapshot_or_block_writer(self):
        row = event_record('action', 'completed', reason='x'*32000)
        self.journal.append(row)
        self.journal.append(row)
        expected = self.journal.read()
        with self.journal.iter_records() as rows:
            failures = []
            def write():
                try:
                    for _ in range(8):
                        self.journal.append(row)
                except Exception as error:
                    failures.append(error)
            thread = threading.Thread(target=write, daemon=True)
            thread.start(); thread.join(3)
            self.assertFalse(thread.is_alive(), 'export blocks writer')
            self.assertFalse(failures)
            self.assertEqual(list(rows), expected)

    def test_partial_consumption_and_output_failure_close_all_descriptors(self):
        self.journal.append(event_record('action', 'completed'))
        from monit_docker import audit
        opened = []
        fdopen = audit.os.fdopen
        def track(*args, **kwargs):
            stream = fdopen(*args, **kwargs)
            opened.append(stream)
            return stream
        with patch('monit_docker.audit.os.fdopen', side_effect=track):
            with self.assertRaises(BrokenPipeError):
                with self.journal.iter_records() as rows:
                    next(rows)
                    raise BrokenPipeError()
        self.assertTrue(opened)
        self.assertTrue(all(stream.closed for stream in opened))

    def test_oversized_lines_and_truncated_snapshots_are_rejected(self):
        self.path.write_bytes(b'x'*65537+b'\n')
        with self.assertRaises(AuditError):
            with self.journal.iter_records():
                pass
        self.path.write_bytes(encoded(event_record('action', 'completed')))
        with self.journal.iter_records() as rows:
            self.path.write_bytes(b'')
            with self.assertRaises(AuditError):
                list(rows)
