"""Durability, outcome truthfulness, attribution and notification boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import csv
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from monit_docker.audit import AuditError, AuditJournal, event_record, export_events, notification_delivery
from monit_docker.audit_forward import NoRedirect, send_events
from monit_docker.notification_audit import NotificationAudit
from monit_docker.domain.errors import ActionRejected, MonitoringError
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService
from test_manual_actions import ID, TOKEN, ORIGIN, payload, snapshot
import test_manual_actions as manual_tests
from test_engine import RuleParser
import test_engine as engine_tests
from test_monit_docker import container
import test_monit_docker as legacy_tests


def alert_payload():
    return dict(version='4', receiver='email-and-slack', status='firing', groupKey='test',
                alerts=[dict(status='firing', labels={'alertname': 'Down'},
                             annotations={'secret': 'DO-NOT-STORE'}, fingerprint='abc',
                             startsAt='2026-09-23T12:00:00Z')])


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'audit/events.jsonl'
        self.journal = AuditJournal(self.path, max_bytes=65536, files=3, emit=False)

    def test_rotation_survives_restart_and_bounds_disk_usage(self):
        for n in range(200):
            self.journal.record('action', 'completed', actor='u' * 500, reason=str(n))
        restarted = AuditJournal(self.path, max_bytes=65536, files=3, emit=False)
        records = restarted.read()
        numbers = [int(row['reason']) for row in records]
        self.assertEqual(numbers, list(range(numbers[0], 200)))
        self.assertGreater(numbers[0], 0)
        files = list(self.path.parent.glob('events.jsonl*'))
        self.assertEqual(len(files), 4)  # three data files and a stable lock
        for path in files:
            self.assertLessEqual(path.stat().st_size, 65536)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertTrue(records[-1]['timestamp'].endswith('Z'))
        self.assertIsNotNone(datetime.fromisoformat(records[-1]['timestamp'].replace('Z', '+00:00')).tzinfo)

    def test_concurrent_instances_do_not_interleave_or_lose_records(self):
        journals = [AuditJournal(self.path, emit=False) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: journals[n % 4].record('action', 'completed', reason=str(n)), range(80)))
        records = self.journal.read()
        self.assertEqual({r['reason'] for r in records}, {str(n) for n in range(80)})
        self.assertEqual(len({r['event_id'] for r in records}), 80)

    def test_truncated_file_is_reported_and_never_appended_to(self):
        self.journal.record('action', 'started')
        with self.path.open('ab') as stream:
            stream.write(b'{"schema_version":1')
        original = self.path.read_bytes()
        with self.assertRaises(AuditError):
            self.journal.read()
        with self.assertRaises(AuditError):
            self.journal.record('action', 'started')
        self.assertEqual(self.path.read_bytes(), original)

    def test_symlink_destination_is_not_followed(self):
        self.path.parent.mkdir()
        target = self.path.parent / 'target'
        target.write_text('preserve')
        self.path.symlink_to(target)
        with self.assertRaises(AuditError):
            self.journal.record('action', 'started')
        self.assertEqual(target.read_text(), 'preserve')

    def test_jsonl_and_csv_exports_and_formula_safety(self):
        record = event_record('action', 'completed', actor='=CMD()', container_name='a,"b\nsecond')
        out = io.StringIO()
        export_events([record], out)
        self.assertEqual(json.loads(out.getvalue()), record)
        out = io.StringIO()
        export_events([record], out, 'csv')
        row = next(csv.DictReader(io.StringIO(out.getvalue())))
        self.assertEqual(row['actor'], "'=CMD()")
        self.assertEqual(row['container_name'], record['container_name'])

    def test_completion_disk_failure_emits_truthful_fallback_without_retry(self):
        output = io.StringIO()
        with patch('monit_docker.audit.os.fsync', side_effect=OSError('secret path')), redirect_stderr(output):
            result = self.journal.finish('action', 'completed', result='succeeded')
        self.assertEqual(result['result'], 'succeeded')
        self.assertEqual(self.journal.failures, 1)
        self.assertIn('"result":"succeeded"', output.getvalue())
        self.assertNotIn('secret path', output.getvalue())

    def test_native_notification_ack_and_failure_are_separate_outcomes(self):
        with notification_delivery(self.journal, 'http', 'one', source='manual', actor='alice'):
            pass
        with self.assertRaises(TimeoutError):
            with notification_delivery(self.journal, 'redis', 'two'):
                raise TimeoutError('secret url')
        records = self.journal.read()
        self.assertEqual([r['delivery_status'] for r in records], ['pending', 'accepted', 'pending', 'failed'])
        self.assertEqual(records[0]['correlation_id'], records[1]['correlation_id'])
        self.assertNotIn('secret url', json.dumps(records))
        with patch.object(self.journal, 'record', side_effect=AuditError('disk full')):
            operation = Mock()
            with self.assertRaises(AuditError):
                with notification_delivery(self.journal, 'http', 'three'):
                    operation()
            operation.assert_not_called()


def make_monitor(journal, execute=None):
    actions = ManualActions(execute or Mock(), ORIGIN, TOKEN, audit=journal)
    monitor = MonitorService(Mock(return_value=snapshot()), manual_actions=actions)
    monitor.run_cycle()
    return actions, monitor


class ActionAuditTests(unittest.TestCase):
    setUp = JournalTests.setUp

    def test_manual_lifecycle_and_rejection(self):
        actions, monitor = make_monitor(self.journal)
        actions.submit(payload(), monitor.status(), actor='alice')
        actions.submit(payload(), monitor.status(), actor='alice')  # idempotent
        with self.assertRaises(ActionRejected):
            actions.submit(payload(), monitor.status(), actor='bob')
        monitor.run_pending_action()
        records = self.journal.read()
        self.assertEqual([r['event'] for r in records], ['queued', 'rejected', 'started', 'completed'])
        self.assertEqual(records[-1]['actor'], 'alice')
        self.assertEqual(records[-1]['source'], 'manual')
        self.assertEqual(records[-1]['result'], 'succeeded')
        self.assertIsInstance(records[-1]['duration_ms'], int)
        self.assertEqual(records[-1]['correlation_id'], payload()['request_id'])
        actions.execute.assert_called_once_with(ID, 'restart')


    def test_manual_failure_and_unavailable_journal(self):
        actions, monitor = make_monitor(self.journal, Mock(side_effect=MonitoringError(118, 'secret')))
        actions.submit(payload(), monitor.status())
        with self.assertLogs('monit-docker', level='ERROR'):
            monitor.run_pending_action()
        record = self.journal.read()[-1]
        self.assertEqual((record['result'], record['error_code']), ('failed', 118))
        self.assertNotIn('secret', json.dumps(record))
        actions, monitor = make_monitor(self.journal)
        actions.submit(payload(2), monitor.status())
        with patch.object(self.journal, 'record', side_effect=AuditError('disk full')):
            with self.assertLogs('monit-docker', level='ERROR'):
                monitor.run_pending_action()
        actions.execute.assert_not_called()
        self.assertEqual(actions.status()['recent'][0]['error'], 'audit_unavailable')


    def test_automatic_success_failure_simulation_and_write_before_execute(self):
        obj = container()
        engine, _, _ = engine_tests.EngineTests.make_engine(self, obj)
        engine.audit = self.journal
        rule = RuleParser().parse('restart')
        engine.run_once(rules=(rule,))
        records = self.journal.read()
        self.assertEqual([r['result'] for r in records], ['pending', 'succeeded'])
        self.assertEqual(records[-1]['actor'], 'rule-engine')
        self.assertEqual(records[0]['correlation_id'], records[1]['correlation_id'])
        engine.run_once(rules=(rule,), dry_run=True)
        self.assertEqual(self.journal.read()[-1]['result'], 'simulated')
        engine.executor.execute = Mock(side_effect=MonitoringError(116, 'secret command'))
        with self.assertRaises(MonitoringError):
            engine.run_once(rules=(rule,))
        self.assertEqual(self.journal.read()[-1]['result'], 'failed')
        self.assertNotIn('secret command', json.dumps(self.journal.read()))
        engine.executor.execute.reset_mock()
        with patch.object(self.journal, 'record', side_effect=AuditError('disk full')), self.assertRaises(AuditError):
            engine.run_once(rules=(rule,))
        engine.executor.execute.assert_not_called()


class AuditHttpTests(unittest.TestCase):
    close_server = manual_tests.ManualHttpTests.close_server
    request = manual_tests.ManualHttpTests.request

    def setUp(self):
        JournalTests.setUp(self)
        manual_tests.ManualHttpTests.setUp(self)
        self.actions.audit = self.journal
        self.monitor.notification_audit = NotificationAudit(self.journal, 'c' * 64)

    def test_untrusted_actor_is_ignored_and_opt_in_requires_proxy_identity(self):
        self.assertEqual(self.request(headers={'X-Monit-Actor': 'forged'})[0], 202)
        self.assertEqual(self.journal.read()[0]['actor'], 'anonymous')
        self.monitor.run_pending_action()
        self.monitor.run_cycle()
        self.actions.trust_actor = True
        self.assertEqual(self.request(body=json.dumps(payload(2)))[0], 403)
        self.assertEqual(self.request(body=json.dumps(payload(2)), headers={'X-Monit-Actor': 'alice'})[0], 202)
        self.assertEqual(self.journal.read()[-1]['actor'], 'alice')

    def test_notifications_require_separate_token_and_never_claim_delivery(self):
        body = json.dumps(alert_payload())
        self.assertEqual(self.request(body=body, path='/v1/notifications')[0], 403)
        code, data = self.request(body=body, path='/v1/notifications', headers={'Authorization': 'Bearer ' + 'c' * 64})
        self.assertEqual((code, data), (202, {'recorded': 1, 'delivery_status': 'not_reported'}))
        record = self.journal.read()[0]
        self.assertEqual(record['alert_status'], 'firing')
        self.assertNotIn('DO-NOT-STORE', json.dumps(record))
        value = alert_payload()
        value['alerts'][0]['status'] = value['status'] = 'resolved'
        self.monitor.notification_audit.receive(value)
        self.assertEqual(record['notification_id'], self.journal.read()[-1]['notification_id'])

    def test_native_route_limits_accept_exact_boundaries_independently(self):
        body = json.dumps(payload()).ljust(1024)
        self.assertEqual(self.request(body=body)[0], 202)
        body = json.dumps(alert_payload()).ljust(65536)
        headers = {'Authorization': 'Bearer ' + 'c' * 64}
        self.assertEqual(self.request(body=body, path='/v1/notifications', headers=headers)[0], 202)
        self.assertEqual(self.request(body=body + ' ', path='/v1/notifications', headers=headers)[0], 413)
        self.assertEqual(self.request(body=json.dumps(payload()).ljust(1025))[0], 413)
        self.assertEqual(len(self.journal.read()), 2)

    def test_bad_batch_has_no_partial_records_and_disk_failure_is_not_acknowledged(self):
        value = alert_payload()
        value['alerts'].append({'status': 'invalid'})
        headers = {'Authorization': 'Bearer ' + 'c' * 64}
        self.assertEqual(self.request(body=json.dumps(value), path='/v1/notifications', headers=headers)[0], 400)
        self.assertFalse(self.path.exists())
        with patch.object(self.journal, 'record', side_effect=AuditError('disk full')):
            self.assertEqual(self.request(body=json.dumps(alert_payload()), path='/v1/notifications', headers=headers)[0], 503)
        self.assertEqual(self.request(body='x' * 65537, path='/v1/notifications', headers=headers)[0], 413)


class ForwardingTests(unittest.TestCase):
    def test_https_acknowledgement_and_stable_idempotency_key(self):
        event = event_record('action', 'completed', result='succeeded')
        response = Mock(status=202)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            self.assertEqual(send_events([event], 'https://sink.example/events'), 1)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Idempotency-key'), event['event_id'])
            self.assertEqual(json.loads(request.data), event)
        for url in ('http://sink.example', 'https://u:p@sink.example', 'https://sink.example/#secret', 'https://sink.example/\n'):
            with self.assertRaises(ValueError):
                send_events([event], url)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://elsewhere'))

    def test_failure_preserves_original_records_and_hides_remote_details(self):
        events = [event_record('action', 'completed')]
        before = json.dumps(events)
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = urllib.error.URLError('secret destination')
            with self.assertRaises(AuditError) as raised:
                send_events(events, 'https://sink.example/events')
        self.assertNotIn('secret destination', str(raised.exception))
        self.assertEqual(json.dumps(events), before)


class AuditCliTests(unittest.TestCase):
    setUp = legacy_tests.RegressionTests.setUp
    invoke = legacy_tests.RegressionTests.invoke

    def test_offline_export_filter_does_not_connect_to_docker(self):
        path = Path(self.temp.name) / 'events.jsonl'
        journal = AuditJournal(path, emit=False)
        journal.record('action', 'completed')
        journal.record('notification', 'received')
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.invoke('--audit-file', str(path), 'audit-export', '--category', 'notification',
                                         '--since', '2020-01-01T00:00:00Z'), 0)
        self.assertEqual(json.loads(output.getvalue())['category'], 'notification')
        self.client.containers.list.assert_not_called()
        with self.assertRaises(SystemExit):
            self.invoke('--audit-file', str(path), 'audit-export', '--since', '2020-01-01')
