"""Bounded browsing, rotation consistency and private HTTP access."""

from monit_docker.adapters.http_security import HttpSecurity
from contextlib import redirect_stderr
import csv
import fcntl
import http.client
import io
import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from monit_docker.audit import AuditJournal, encoded, event_record
from monit_docker.adapters.audit_http import read_page, query_error_status
from monit_docker.audit_query import AuditReader, QueryError, SCAN_BYTES, PAGE_BYTES
from monit_docker.adapters.http import StatusServer
from monit_docker.service import MonitorService

TOKEN = 'd' * 64
HEADERS = {'X-Monit-Audit-Token': TOKEN, 'X-Monit-Actor': 'alice'}


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.jsonl'
        self.journal = AuditJournal(self.path, max_bytes=65536, files=3, emit=False)
        self.reader = AuditReader(self.journal)

    def populate(self, count, **fields):
        return [self.journal.record('action', 'completed', reason=str(i), source='manual', result='succeeded', **fields)
                for i in range(count)]

    def test_reverse_pages_do_not_skip_duplicate_or_include_new_appends(self):
        records = self.populate(150)
        first, _ = read_page(self.reader, '', 'alice')
        self.assertEqual([r['event_id'] for r in first['records']], [r['event_id'] for r in records[::-1][:100]])
        self.populate(5)
        second, _ = read_page(self.reader, urlencode({'cursor': first['next_cursor']}), 'alice')
        self.assertEqual([r['event_id'] for r in second['records']], [r['event_id'] for r in records[::-1][100:]])
        self.assertIsNone(second['next_cursor'])
        repeat, _ = read_page(self.reader, urlencode({'cursor': first['page_cursor'], 'format': 'csv'}), 'alice')
        self.assertEqual(repeat['records'], first['records'])

    def test_rotation_expires_only_when_unread_snapshot_data_is_lost(self):
        self.populate(120)
        first, _ = read_page(self.reader, '', 'alice')
        self.populate(40)  # rotation preserves the original inode
        page, _ = read_page(self.reader, urlencode({'cursor': first['next_cursor']}), 'alice')
        self.assertTrue(page['records'])
        self.populate(500)
        with self.assertRaises(QueryError) as error:
            read_page(self.reader, urlencode({'cursor': first['next_cursor']}), 'alice')
        self.assertEqual(query_error_status(error.exception), 410)

    def test_filters_scope_and_cursor_integrity(self):
        self.populate(120, container_name='web', actor='=CMD()\n')
        query = dict(container='WEB', source='manual', category='action', result='succeeded', since='2020-01-01T00:00:00Z')
        first, _ = read_page(self.reader, urlencode(query), 'alice')
        self.assertEqual(len(first['records']), 100)
        self.assertEqual(first['records'][0]['actor'], r'\u003dCMD()\u000a')
        query['cursor'] = first['next_cursor']
        self.assertTrue(read_page(self.reader, urlencode(query), 'alice')[0]['records'])
        for actor, params in [('bob', query), ('alice', dict(query, result='failed')), ('alice', dict(query, cursor=query['cursor']+'x'))]:
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, urlencode(params), actor)
            self.assertEqual(query_error_status(error.exception), 400)
        with patch('monit_docker.audit_query.time.time', return_value=10**12), self.assertRaises(QueryError) as error:
            read_page(self.reader, urlencode(query), 'alice')
        self.assertEqual(query_error_status(error.exception), 410)

    def test_sparse_search_and_large_records_have_bounded_progress(self):
        record = event_record('action', 'completed', reason='x' * 30000)
        self.path.write_bytes(encoded(record) * 100)
        query = {'result': 'failed'}
        first, _ = read_page(self.reader, urlencode(query), 'alice')
        self.assertFalse(first['records'])
        self.assertTrue(first['next_cursor'])
        self.assertLessEqual(first['scanned_bytes'], SCAN_BYTES)
        query['cursor'] = first['next_cursor']
        second, _ = read_page(self.reader, urlencode(query), 'alice')
        self.assertNotEqual(first['next_cursor'], second['next_cursor'])
        page, _ = read_page(self.reader, '', 'alice')
        self.assertLessEqual(sum(len(encoded(row)) for row in page['records']), PAGE_BYTES)
        self.assertGreater(len(page['records']), 0)

    def test_correlation_filter_is_exact_and_bound_to_cursor_and_exports(self):
        wanted = self.populate(105, correlation_id='request-A', container_name='web')
        self.populate(1, correlation_id='request-A-extra')
        self.populate(1, correlation_id='request-a')
        self.populate(1)
        query = dict(correlation_id='request-A', category='action', container='web',
                     source='manual', result='succeeded', since='2020-01-01T00:00:00Z',
                     until='2100-01-01T00:00:00Z')
        first, _ = read_page(self.reader, urlencode(query), 'alice')
        self.assertEqual(first['records'], wanted[::-1][:100])
        second, _ = read_page(self.reader, urlencode(dict(query, cursor=first['next_cursor'])), 'alice')
        self.assertEqual(second['records'], wanted[::-1][100:])
        exported, format = read_page(self.reader, urlencode(dict(query, cursor=first['page_cursor'], format='csv')), 'alice')
        self.assertEqual(format, 'csv')
        self.assertEqual(exported['records'], first['records'])
        for changed in ('request-a', 'request-A-extra'):
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, urlencode(dict(query, cursor=first['next_cursor'], correlation_id=changed)), 'alice')
            self.assertEqual(query_error_status(error.exception), 400)
        for query in ('correlation_id=a&correlation_id=b', 'correlation_id=%0A', 'correlation_id=' + 'x' * 257):
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, query, 'alice')
            self.assertEqual(query_error_status(error.exception), 400)

    def test_scan_releases_writer_lock_and_persistent_contention_is_bounded(self):
        self.populate(2)
        lock = os.open(str(self.path) + '.lock', os.O_RDWR)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaises(QueryError) as error:
            read_page(self.reader, '', 'alice')
        self.assertEqual(error.exception.reason, 'audit_busy')
        fcntl.flock(lock, fcntl.LOCK_UN)
        original = os.pread
        def read(*args):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
            return original(*args)
        with patch('monit_docker.audit_query.os.pread', side_effect=read):
            self.assertTrue(read_page(self.reader, '', 'alice')[0]['records'])
        self.reader._slots.acquire(); self.reader._slots.acquire()
        try:
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, '', 'alice')
            self.assertEqual(error.exception.reason, 'audit_busy')
        finally:
            self.reader._slots.release(); self.reader._slots.release()

    def test_short_writer_contention_recovers_within_same_read(self):
        records = self.populate(2)
        lock = os.open(str(self.path) + '.lock', os.O_RDWR)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        def release_writer(delay):
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, .002)
            fcntl.flock(lock, fcntl.LOCK_UN)
        with patch('monit_docker.audit_query.time.sleep', side_effect=release_writer) as sleep:
            self.assertEqual(read_page(self.reader, '', 'alice')[0]['records'], records[::-1])
        sleep.assert_called_once()

    def test_writer_wait_deadline_releases_query_slot(self):
        self.populate(1)
        lock = os.open(str(self.path) + '.lock', os.O_RDWR)
        self.addCleanup(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch('monit_docker.audit_query.time.monotonic', side_effect=[10, 10.099, 10.101]), \
                patch('monit_docker.audit_query.time.sleep') as sleep:
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, '', 'alice')
        self.assertEqual(error.exception.reason, 'audit_busy')
        self.assertAlmostEqual(sleep.call_args.args[0], .001)
        fcntl.flock(lock, fcntl.LOCK_UN)
        self.assertTrue(read_page(self.reader, '', 'alice')[0]['records'])
        self.assertTrue(self.reader._slots.acquire(False))
        self.assertTrue(self.reader._slots.acquire(False))
        self.reader._slots.release(); self.reader._slots.release()

    def test_malformed_queries_files_and_missing_history(self):
        self.assertEqual(read_page(self.reader, '', 'alice')[0]['records'], [])
        for query in ('x=1', 'source=a', 'format=html', 'since=2026-01-01', 'source=manual&source=automatic',
                      'since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z', 'container=%0A', 'cursor=x'):
            with self.subTest(query=query), self.assertRaises(QueryError) as error:
                read_page(self.reader, query, 'alice')
            self.assertEqual(query_error_status(error.exception), 400)
        for value in (b'{bad}\n', b'{incomplete', b'x'*150000+b'\n'):
            self.path.write_bytes(value)
            with self.assertRaises(QueryError) as error:
                read_page(self.reader, '', 'alice')
            self.assertEqual(query_error_status(error.exception), 503)
        self.path.unlink()
        outside = Path(self.temp.name) / 'outside'
        outside.write_text('preserve')
        self.path.symlink_to(outside)
        with self.assertRaises(QueryError):
            read_page(self.reader, '', 'alice')
        self.assertEqual(outside.read_text(), 'preserve')


class AuditReadHttpTests(unittest.TestCase):
    def setUp(self):
        QueryTests.setUp(self)
        self.monitor = MonitorService(Mock(), audit_reader=self.reader)
        self.server = StatusServer(('127.0.0.1', 0), self.monitor, HttpSecurity(audit_token=TOKEN))
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown(); self.thread.join(3); self.server.server_close()

    def request(self, path='/v1/audit', headers=HEADERS, method='GET'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), {key.lower(): value for key, value in response.getheaders()}
        finally:
            connection.close()

    def test_disabled_anonymous_and_wrong_secret_access_are_denied(self):
        for path in ('/v1/audit', '/v1/audit/export'):
            self.assertEqual(self.request(path, {})[0], 403)
            self.assertEqual(self.request(path, {'X-Monit-Audit-Token': TOKEN})[0], 403)
            self.assertEqual(self.request(path, dict(HEADERS, **{'X-Monit-Audit-Token': 'e'*64}))[0], 403)
        for path in ('/v1//audit', '/v1//audit/export'):
            self.assertEqual(self.request(path, HEADERS)[0], 404)
        self.monitor.audit_reader = None
        self.assertEqual(self.request()[0], 404)
        self.assertFalse(self.monitor.status()['audit_enabled'])

    def test_export_uses_the_exact_displayed_snapshot_and_safe_text(self):
        record = self.journal.record('action', 'completed', actor='=CMD()\n', result='succeeded')
        code, body, headers = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(headers['cache-control'], 'no-store')
        data = json.loads(body)
        self.assertEqual(data['records'], [record])
        self.journal.record('action', 'completed')
        query = urlencode({'cursor': data['page_cursor'], 'format': 'csv'})
        code, body, headers = self.request('/v1/audit/export?' + query)
        self.assertEqual(code, 200)
        self.assertIn('attachment;', headers['content-disposition'])
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['actor'], r'\u003dCMD()\u000a')
        code, body, _ = self.request('/v1/audit/export?' + urlencode({'cursor': data['page_cursor']}))
        self.assertEqual(json.loads(body), record)
        self.assertEqual(self.request(method='HEAD')[1], b'')
        self.assertEqual(self.request(method='POST')[0], 405)
        self.assertEqual(self.request('/v1/audit?source=invalid')[0], 400)
        self.assertNotIn(TOKEN, json.dumps(self.monitor.status()))

    def test_http_correlation_query_returns_all_action_stages(self):
        records = [self.journal.record('action', event, correlation_id='request-123', result=result)
                   for event, result in (('queued', 'pending'), ('started', 'pending'), ('completed', 'failed'))]
        self.journal.record('action', 'completed', correlation_id='request-123-other')
        self.journal.record('notification', 'received', correlation_id='request-123')
        code, body, _ = self.request('/v1/audit?category=action&correlation_id=request-123')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['records'], records[::-1])

