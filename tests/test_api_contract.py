"""Public wire contracts: names, units, capability discovery and failure meaning."""

import csv
import http.client
import io
import json
from pathlib import Path
import re
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

from monit_docker.adapters.http import StatusServer
from monit_docker.audit import AuditJournal
from monit_docker.audit_query import AuditReader
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.manual_actions import ManualActions
from monit_docker.notification_audit import NotificationAudit
from monit_docker.service import MonitorService


_ID = 'a' * 64
_TOKEN = 'b' * 64
_ORIGIN = 'https://monitor.example'
_ACTION_HEADERS = {'Content-Type': 'application/json', 'Origin': _ORIGIN,
                   'X-Monit-Action-Token': _TOKEN, 'X-Monit-Actor': 'operator'}
_AUDIT_HEADERS = {'X-Monit-Audit-Token': _TOKEN, 'X-Monit-Actor': 'operator'}
_STATUS_FIELDS = frozenset(('api_version', 'running', 'ready', 'last_cycle_success',
    'last_cycle_finished_at', 'last_success_at', 'age_seconds', 'last_error_code',
    'cycles_total', 'errors_total', 'actions', 'containers', 'manual_actions', 'audit_enabled'))
_OUTCOMES = frozenset(('executed', 'cooldown', 'pending', 'restart-limit', 'maintenance', 'dry-run'))
_CONTAINER_FIELDS = frozenset(('id', 'name', 'status', 'pid', 'health', 'mem_usage',
    'mem_limit', 'mem_percent', 'cpu_percent', 'io_read', 'io_write', 'net_rx', 'net_tx',
    'oom_events', 'starts_recent', 'pids_current', 'pids_limit', 'pids_percent',
    'event_window_seconds', 'event_window_end', 'event_history_complete',
    'manual_actions_protected', 'restart_attempts', 'restart_limit',
    'maintenance_active', 'maintenance_until', 'filesystems'))
_REQUEST_FIELDS = frozenset(('request_id', 'container_id', 'action', 'actor',
    'container_name', 'status', 'submitted_at', 'finished_at', 'error', 'error_code'))
_PAGE_FIELDS = frozenset(('records', 'next_cursor', 'page_cursor', 'scanned_bytes', 'limit'))
_METRIC_SAMPLE = re.compile(r'^(monit_docker_[a-z_]+)(?:\{(.*)\})? ([^ ]+)$')
_LABEL_KEY = re.compile(r'(?:^|,)([a-z_]+)="(?:\\.|[^"\\])*"')
_BASE_LABELS = frozenset(('id', 'name'))
_FILESYSTEM_LABELS = _BASE_LABELS | frozenset(('group', 'path'))
_CONTAINER_GAUGES = (
    'restart_attempts', 'restart_limit', 'restart_blocked', 'maintenance_active',
    'maintenance_until_seconds', 'pids_current', 'pids_limit', 'pids_percent',
    'event_history_complete', 'event_window_end', 'memory_usage_bytes',
    'memory_limit_bytes', 'memory_usage_percent', 'cpu_usage_percent')
_CONTAINER_COUNTERS = ('io_read_bytes_total', 'io_write_bytes_total',
                       'network_receive_bytes_total', 'network_transmit_bytes_total')
_FILESYSTEM_GAUGES = ('disk_usage_bytes', 'disk_available_bytes', 'disk_total_bytes',
    'disk_usage_percent', 'inode_usage', 'inode_available', 'inode_total',
    'inode_usage_percent', 'filesystem_read_only', 'fs_readable', 'fs_writable', 'fs_executable')
_METRICS = {
    'ready': ('gauge', frozenset()),
    'cycle_running': ('gauge', frozenset()),
    'cycles_total': ('counter', frozenset()),
    'cycle_errors_total': ('counter', frozenset()),
    'last_success_timestamp_seconds': ('gauge', frozenset()),
    'action_decisions_total': ('counter', frozenset(('outcome',))),
    'container_info': ('gauge', _BASE_LABELS | frozenset(('status',))),
    'container_health_status': ('gauge', _BASE_LABELS | frozenset(('health',))),
    'container_oom_events': ('gauge', _BASE_LABELS | frozenset(('window_seconds',))),
    'container_starts_recent': ('gauge', _BASE_LABELS | frozenset(('window_seconds',))),
}
_METRICS.update(('container_' + name, ('gauge', _BASE_LABELS)) for name in _CONTAINER_GAUGES)
_METRICS.update(('container_' + name, ('counter', _BASE_LABELS)) for name in _CONTAINER_COUNTERS)
_METRICS.update(('container_' + name, ('gauge', _FILESYSTEM_LABELS)) for name in _FILESYSTEM_GAUGES)


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=1000)
        self.ticks = Mock(return_value=10)
        self.cycle = Mock(return_value=self.snapshot())
        self.monitor = MonitorService(self.cycle, stale_after=3,
                                      clock=self.clock, monotonic=self.ticks)
        self.server = StatusServer(('127.0.0.1', 0), self.monitor)
        self.thread = Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.close)

    @staticmethod
    def snapshot(**changes):
        fields = dict(id=_ID, name='web', status='running')
        fields.update(changes)
        return CycleResult((ContainerSnapshot(**fields),), ())

    def close(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.assertFalse(self.thread.is_alive())

    def request(self, path, method='GET', payload=None, headers=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            body = None if payload is None else json.dumps(payload)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            result = (response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
        finally:
            connection.close()
        self.assertEqual(result[1]['cache-control'], 'no-store')
        self.assertEqual(result[1]['x-content-type-options'], 'nosniff')
        self.assertEqual(result[1]['referrer-policy'], 'no-referrer')
        return result

    def json_request(self, *args, **kwargs):
        code, headers, body = self.request(*args, **kwargs)
        self.assertEqual(headers['content-type'], 'application/json; charset=utf-8')
        self.assertTrue(body.endswith(b'\n'))
        return code, json.loads(body)

    def enable_actions(self):
        self.execute = Mock()
        actions = ManualActions(self.execute, _ORIGIN, _TOKEN, clock=self.clock,
                                monotonic=self.ticks, trust_actor=True)
        self.monitor.manual_actions = actions
        return actions

    def enable_audit(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        journal = AuditJournal(Path(temporary.name) / 'events.jsonl', emit=False)
        self.monitor.audit_reader = AuditReader(journal, _TOKEN)
        return journal

    @staticmethod
    def payload(**changes):
        value = dict(request_id='1' * 32, container_id=_ID, action='restart')
        value.update(changes)
        return value

    def test_status_fields_nulls_and_degraded_http_semantics(self):
        code, startup = self.json_request('/v1/status')
        self.assertEqual(code, 200)
        self.assertLessEqual(_STATUS_FIELDS, startup.keys())
        self.assertEqual(startup['api_version'], 1)
        self.assertEqual(startup['manual_actions'], {'enabled': False})
        self.assertIs(startup['audit_enabled'], False)
        self.assertEqual(startup['actions'], dict.fromkeys(_OUTCOMES, 0))
        for field in ('last_cycle_finished_at', 'last_success_at', 'last_error_code', 'age_seconds'):
            self.assertIsNone(startup[field])
        self.assertEqual(self.json_request('/readyz'), (503, {'ready': False}))
        self.assertEqual(self.json_request('/healthz'), (200, {'alive': True}))
        self.cycle.assert_not_called()

        self.monitor.run_cycle()
        ready = self.json_request('/v1/status')[1]
        self.assertLessEqual(_CONTAINER_FIELDS, ready['containers'][0].keys())
        self.assertEqual(ready['containers'][0]['filesystems'], [])
        for field in ('cpu_percent', 'pid', 'restart_limit', 'event_window_end', 'maintenance_until'):
            self.assertIsNone(ready['containers'][0][field])
        self.assertIs(ready['containers'][0]['manual_actions_protected'], False)
        self.ticks.return_value = 13
        self.assertEqual(self.json_request('/readyz'), (200, {'ready': True}))
        self.ticks.return_value = 13.01
        code, stale = self.json_request('/v1/status')
        self.assertEqual((code, stale['containers'], stale['last_error_code']), (200, [], None))
        self.assertEqual((stale['last_success_at'], stale['errors_total']), (1000, 0))
        self.assertIs(stale['last_cycle_success'], True)
        self.assertEqual(self.json_request('/readyz'), (503, {'ready': False}))

        self.cycle.side_effect = MonitoringError(170, 'private-details')
        with self.assertLogs('monit-docker', level='ERROR'):
            self.monitor.run_cycle()
        failed = self.json_request('/v1/status')[1]
        self.assertEqual((failed['last_error_code'], failed['errors_total'], failed['cycles_total']), (170, 1, 2))
        self.assertEqual(failed['last_success_at'], 1000)
        self.assertNotIn('private-details', json.dumps(failed))
        self.cycle.side_effect = None
        self.monitor.run_cycle()
        self.assertEqual(self.json_request('/readyz'), (200, {'ready': True}))

    def test_running_cycle_keeps_a_fresh_previous_snapshot_readable(self):
        self.monitor.run_cycle()
        entered, release = Event(), Event()

        def collect(observer):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return self.snapshot()

        self.cycle.side_effect = collect
        worker = Thread(target=self.monitor.run_cycle)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            code, status = self.json_request('/v1/status')
            self.assertEqual(code, 200)
            self.assertIs(status['running'], True)
            self.assertIs(status['ready'], True)
            self.assertEqual(status['cycles_total'], 1)
            self.assertEqual(status['containers'][0]['id'], _ID)
            self.assertEqual(self.json_request('/readyz'), (200, {'ready': True}))
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_metric_names_types_labels_units_and_known_zeroes(self):
        filesystem = dict(group='data', path='/srv/data', disk_usage=10, disk_available=30,
                          disk_total=50, disk_percent=25, inode_usage=2, inode_available=8,
                          inode_total=10, inode_percent=20, fs_mode='rw', fs_readable=1,
                          fs_writable=0, fs_executable=1)
        self.cycle.return_value = self.snapshot(
            health='healthy', mem_usage=1024, mem_limit=2048, mem_percent=50,
            cpu_percent=230, io_read=12, io_write=13, net_rx=14, net_tx=15,
            oom_events=0, starts_recent=1, pids_current=2, pids_limit=8, pids_percent=25,
            event_history_complete=1, event_window_seconds=300, event_window_end=1000,
            restart_attempts=3, restart_limit=3, maintenance_active=True,
            maintenance_until=1900, filesystems=[filesystem])
        self.monitor.run_cycle()
        code, headers, body = self.request('/metrics')
        self.assertEqual((code, headers['content-type']), (200, 'text/plain; version=0.0.4; charset=utf-8'))
        self.assertTrue(body.endswith(b'\n'))
        lines = body.decode().splitlines()
        declarations = {line.split()[2].removeprefix('monit_docker_'): line.split()[3]
                        for line in lines if line.startswith('# TYPE ')}
        self.assertLessEqual(_METRICS.keys(), declarations.keys())
        samples = {}
        for line in lines:
            if line.startswith('#'):
                continue
            match = _METRIC_SAMPLE.fullmatch(line)
            self.assertIsNotNone(match, line)
            name, labels, value = match.groups()
            name = name.removeprefix('monit_docker_')
            if name in _METRICS:
                kind, keys = _METRICS[name]
                self.assertEqual(declarations[name], kind, name)
                self.assertEqual(frozenset(_LABEL_KEY.findall(labels or '')), keys, name)
            samples.setdefault(name, []).append(float(value))
        self.assertLessEqual(_METRICS.keys(), samples.keys())
        for name, expected in (('container_cpu_usage_percent', 230),
                               ('container_memory_usage_bytes', 1024),
                               ('container_disk_usage_percent', 25),
                               ('container_network_receive_bytes_total', 14),
                               ('container_fs_writable', 0), ('container_oom_events', 0),
                               ('container_restart_blocked', 1),
                               ('container_maintenance_until_seconds', 1900)):
            self.assertEqual(samples[name], [expected], name)
        self.assertEqual(samples['container_health_status'], [1])
        self.assertEqual(len(samples['action_decisions_total']), 6)
        self.assertNotIn('monit_docker_container_manual_actions_protected', body.decode())

    def test_metric_omission_is_distinct_from_zero_and_preserves_last_success(self):
        initial = self.request('/metrics')[2].decode()
        self.assertNotIn('monit_docker_last_success_timestamp_seconds', initial)
        self.cycle.return_value = self.snapshot(cpu_percent=0)
        self.monitor.run_cycle()
        fresh = self.request('/metrics')[2].decode()
        self.assertIn('monit_docker_container_cpu_usage_percent{id=', fresh)
        self.assertNotIn('monit_docker_container_memory_usage_bytes{', fresh)
        self.ticks.return_value = 14
        code, _, body = self.request('/metrics')
        self.assertEqual(code, 200)
        self.assertIn(b'monit_docker_ready 0\n', body)
        self.assertIn(b'monit_docker_last_success_timestamp_seconds 1000\n', body)
        self.assertIn(b'# TYPE monit_docker_container_cpu_usage_percent gauge\n', body)
        self.assertFalse(any(line.startswith(b'monit_docker_container_') for line in body.splitlines()))
        self.cycle.assert_called_once()

    def test_manual_record_deduplication_actor_and_completion_over_http(self):
        self.enable_actions()
        self.monitor.run_cycle()
        code, record = self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS)
        self.assertEqual((code, record['status'], record['actor']), (202, 'queued', 'operator'))
        self.assertLessEqual(_REQUEST_FIELDS, record.keys())
        self.assertEqual(record['container_name'], 'web')
        for field in ('finished_at', 'error', 'error_code'):
            self.assertIsNone(record[field])
        self.execute.assert_not_called()
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS), (202, record))
        changed_actor = dict(_ACTION_HEADERS, **{'X-Monit-Actor': 'someone-else'})
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), changed_actor),
                         (409, {'error': 'request_id_conflict'}))
        self.monitor.run_pending_action()
        self.execute.assert_called_once_with(_ID, 'restart')
        code, completed = self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS)
        self.assertEqual((code, completed['status'], completed['finished_at']), (202, 'succeeded', 1000))
        status = self.json_request('/v1/status')[1]
        self.assertIs(status['ready'], False)
        self.assertIsNone(status['age_seconds'])
        self.assertEqual(status['manual_actions']['recent'], [completed])
        self.assertEqual(status['actions'], dict.fromkeys(_OUTCOMES, 0))
        self.assertNotIn(_TOKEN, json.dumps(status))
        self.assertFalse(self.monitor.run_pending_action())

    def test_manual_rejections_preserve_machine_readable_http_reasons(self):
        self.enable_actions()
        cases = (
            ([], {}, 400, 'invalid_request'),
            (self.payload(container_id='c' * 64), {}, 409, 'not_selected'),
            (self.payload(action='start'), {}, 409, 'state_changed'),
            (self.payload(action='restart-reset'), {}, 409, 'no_restart_attempts'),
            (self.payload(), {'manual_actions_protected': True}, 403, 'container_protected'),
        )
        for payload, changes, code, reason in cases:
            with self.subTest(reason=reason):
                self.cycle.return_value = self.snapshot(**changes)
                self.monitor.run_cycle()
                self.assertEqual(self.json_request('/v1/actions', 'POST', payload, _ACTION_HEADERS),
                                 (code, {'error': reason}))
        self.cycle.return_value = self.snapshot()
        self.monitor.run_cycle()
        self.ticks.return_value = 14
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS),
                         (409, {'error': 'not_ready'}))
        self.monitor.run_cycle()
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS)[0], 202)
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(request_id='2' * 32), _ACTION_HEADERS),
                         (409, {'error': 'busy'}))
        self.execute.assert_not_called()

    def test_content_type_parameters_and_disabled_capabilities_are_explicit(self):
        self.assertEqual(self.json_request('/v1/audit'), (404, {'error': 'not_found'}))
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), _ACTION_HEADERS),
                         (405, {'error': 'method_not_allowed'}))
        self.assertEqual(self.json_request('/v1/actions'), (404, {'error': 'not_found'}))
        self.enable_actions()
        self.monitor.run_cycle()
        headers = dict(_ACTION_HEADERS, **{'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(self.json_request('/v1/actions', 'POST', self.payload(), headers),
                         (415, {'error': 'json_required'}))
        journal = self.enable_audit()
        self.monitor.notification_audit = NotificationAudit(journal, _TOKEN)
        self.monitor.manual_actions = None
        payload = dict(version='4', receiver='test', groupKey='group', status='firing',
                       alerts=[dict(status='firing', labels={'alertname': 'Down'})])
        headers = {'Content-Type': 'application/json; charset=utf-8', 'Authorization': 'Bearer ' + _TOKEN}
        self.assertEqual(self.json_request('/v1/notifications', 'POST', payload, headers),
                         (202, {'recorded': 1, 'delivery_status': 'not_reported'}))
        self.assertEqual(len(journal.read()), 1)

    def test_audit_page_envelope_export_head_and_cursor_error_codes(self):
        journal = self.enable_audit()
        journal.record('action', 'completed', result='succeeded')
        with patch('monit_docker.audit_query.time.time', return_value=1000):
            code, page = self.json_request('/v1/audit', headers=_AUDIT_HEADERS)
        self.assertEqual(code, 200)
        self.assertLessEqual(_PAGE_FIELDS, page.keys())
        self.assertEqual(page['limit'], 100)
        self.assertIsNone(page['next_cursor'])
        self.assertIsInstance(page['scanned_bytes'], int)
        query = urlencode({'cursor': page['page_cursor'], 'format': 'csv'})
        path = '/v1/audit/export?' + query
        with patch('monit_docker.audit_query.time.time', return_value=1100):
            code, headers, body = self.request(path, headers=_AUDIT_HEADERS)
            head_code, head_headers, head_body = self.request(path, 'HEAD', headers=_AUDIT_HEADERS)
            other_actor = dict(_AUDIT_HEADERS, **{'X-Monit-Actor': 'another-operator'})
            self.assertEqual(self.json_request(path, headers=other_actor), (400, {'error': 'invalid_cursor'}))
        self.assertEqual((code, head_code, head_body), (200, 200, b''))
        self.assertEqual(headers['content-type'], 'text/csv; charset=utf-8')
        self.assertEqual(headers['content-disposition'], 'attachment; filename="monit-docker-events.csv"')
        self.assertEqual(int(head_headers['content-length']), len(body))
        rows = list(csv.DictReader(io.StringIO(body.decode())))
        self.assertEqual(rows[0]['event_id'], page['records'][0]['event_id'])
        with patch('monit_docker.audit_query.time.time', return_value=1901):
            self.assertEqual(self.json_request(path, headers=_AUDIT_HEADERS), (410, {'error': 'cursor_expired'}))
        self.monitor.audit_reader = AuditReader(journal, _TOKEN)
        self.assertEqual(self.json_request(path, headers=_AUDIT_HEADERS), (400, {'error': 'invalid_cursor'}))

    def test_audit_unavailable_and_invalid_filters_are_not_empty_successes(self):
        journal = self.enable_audit()
        self.assertEqual(self.json_request('/v1/audit?limit=10', headers=_AUDIT_HEADERS),
                         (400, {'error': 'invalid_filters'}))
        journal.path.write_text('broken\n')
        self.assertEqual(self.json_request('/v1/audit', headers=_AUDIT_HEADERS),
                         (503, {'error': 'audit_unavailable'}))


if __name__ == '__main__':
    unittest.main()
