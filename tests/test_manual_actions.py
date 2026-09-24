"""Authenticated HTTP requests, queue races and actual engine/state contracts."""

import http.client
import json
from pathlib import Path
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.http import StatusServer
from monit_docker.domain.errors import ActionRejected, MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService
import test_engine as engine_tests
import test_monit_docker as legacy

ID = 'a' * 64
TOKEN = 'b' * 64
ORIGIN = 'https://monitor.example'


def payload(number=1, **changes):
    return dict(dict(request_id='%032x' % number, container_id=ID, action='restart'), **changes)


def snapshot(**changes):
    return CycleResult((ContainerSnapshot(id=ID, name='web', status='running', **changes),), ())


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.execute = Mock()
        self.tick = Mock(return_value=1)
        self.actions = ManualActions(self.execute, ORIGIN, TOKEN, monotonic=self.tick)
        self.monitor = MonitorService(Mock(return_value=snapshot()), manual_actions=self.actions)
        self.monitor.run_cycle()

    def submit(self, value=None):
        return self.actions.submit(value or payload(), self.monitor.status())

    def test_validation_and_stale_data_fail_closed(self):
        for value in ([], None, {}, payload(action='exec'), payload(container_id='a'),
                      payload(request_id=True), dict(payload(), kwargs={})):
            with self.subTest(value=value), self.assertRaises(ActionRejected):
                self.actions.submit(value, self.monitor.status())
        self.monitor._last_success_tick = None
        with self.assertRaisesRegex(ActionRejected, 'not_ready'):
            self.submit()
        self.execute.assert_not_called()

    def test_protected_container_is_visible_but_requests_are_not_queued(self):
        self.monitor.cycle.return_value = snapshot(manual_actions_protected=True)
        self.monitor.run_cycle()
        self.assertTrue(self.monitor.status()['containers'][0]['manual_actions_protected'])
        for action in ('start', 'stop', 'restart', 'restart-reset'):
            with self.subTest(action=action), self.assertRaisesRegex(ActionRejected, 'container_protected'):
                self.submit(payload(action=action))
        self.assertEqual(self.actions.status()['recent'], [])
        self.assertFalse(self.monitor.run_pending_action())
        self.execute.assert_not_called()

    def test_deduplication_busy_conflict_and_results_are_copies(self):
        accepted = self.submit()
        accepted['status'] = 'corrupted'
        self.assertEqual(self.submit()['status'], 'queued')
        with self.assertRaisesRegex(ActionRejected, 'busy'):
            self.submit(payload(2))
        with self.assertRaisesRegex(ActionRejected, 'request_id_conflict'):
            self.submit(payload(action='stop'))
        self.assertTrue(self.monitor.run_pending_action())
        self.assertFalse(self.monitor.status()['ready'])
        self.assertEqual(self.submit()['status'], 'succeeded')
        self.assertFalse(self.monitor.run_pending_action())
        self.execute.assert_called_once_with(ID, 'restart')
        self.monitor.run_cycle()
        self.assertTrue(self.monitor.status()['ready'])

    def test_rearm_requires_a_budget_and_duplicate_request_is_not_replayed(self):
        request = payload(action='restart-reset')
        with self.assertRaisesRegex(ActionRejected, 'no_restart_attempts'):
            self.submit(request)
        self.monitor.cycle.return_value = snapshot(restart_attempts=3, restart_limit=3)
        self.monitor.run_cycle()
        self.assertEqual(self.submit(request)['status'], 'queued')
        self.assertEqual(self.submit(request)['status'], 'queued')
        self.monitor.run_pending_action()
        self.assertEqual(self.submit(request)['status'], 'succeeded')
        self.execute.assert_called_once_with(ID, 'restart-reset')

    def test_expiry_and_bounded_history(self):
        self.submit()
        self.tick.return_value = 62
        self.assertFalse(self.monitor.run_pending_action())
        self.assertEqual(self.actions.status()['recent'][0]['error'], 'expired')
        self.execute.assert_not_called()
        for i in range(2, 40):
            self.submit(payload(i))
            self.monitor.run_pending_action()
            self.monitor.run_cycle()
        self.assertEqual(len(self.actions.status()['recent']), 32)
        self.assertNotIn(TOKEN, json.dumps(self.monitor.status()))

    def test_slow_action_does_not_block_status_and_prevents_cycles_or_writes(self):
        entered, release = Event(), Event()
        self.execute.side_effect = lambda *_: (entered.set(), release.wait(5))
        self.submit()
        worker = Thread(target=self.monitor.run_pending_action)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.monitor.status()['manual_actions']['recent'][0]['status'], 'running')
            self.assertFalse(self.monitor.status()['ready'])
            with self.assertRaises(RuntimeError):
                self.monitor.run_cycle()
            with self.assertRaisesRegex(ActionRejected, 'busy'):
                self.submit(payload(2))
            self.assertFalse(self.monitor.run_pending_action())
        finally:
            release.set()
            worker.join(3)
        self.monitor.run_cycle()

    def test_cycle_blocks_manual_work_and_scheduler_refreshes_after_action(self):
        entered, release = Event(), Event()
        def cycle(observer):
            entered.set()
            release.wait(5)
            return snapshot()
        self.monitor.cycle.side_effect = cycle
        thread = Thread(target=self.monitor.run_cycle)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.submit()
            self.assertFalse(self.monitor.run_pending_action())
            self.execute.assert_not_called()
        finally:
            release.set()
            thread.join(3)
        stop = Event()
        count = []
        def scheduled(observer):
            count.append(1)
            if len(count) == 2:
                stop.set()
            return snapshot()
        self.monitor.cycle.side_effect = scheduled
        self.monitor.run(stop)
        self.assertEqual(len(count), 2)
        self.execute.assert_called_once()

    def test_failure_is_sanitized_and_does_not_break_next_collection(self):
        self.execute.side_effect = MonitoringError(118, 'secret value must not leak')
        self.submit()
        with self.assertLogs('monit-docker', level='ERROR'):
            self.monitor.run_pending_action()
        data = self.monitor.status()
        self.assertEqual(data['manual_actions']['recent'][0]['error_code'], 118)
        self.assertNotIn('secret value', json.dumps(data))
        self.monitor.run_cycle()
        self.assertTrue(self.monitor.status()['ready'])


class ManualEngineTests(unittest.TestCase):
    make_engine = engine_tests.EngineTests.make_engine

    def test_protection_label_defaults_and_explicit_false_values(self):
        for value, protected in ((None, False), ('false', False), (' FALSE ', False),
                                 ('0', False), ('no', False), ('off', False),
                                 ('true', True), ('1', True), ('', True), ('treu', True)):
            with self.subTest(value=value):
                obj = legacy.container()
                obj.attrs['Config'] = {'Labels': {} if value is None else {'monit-docker.protected': value}}
                engine, _, _ = self.make_engine(obj)
                result = engine.run_once(resources=('status',))
                self.assertEqual(result.snapshots[0].manual_actions_protected, protected)

    def test_protection_blocks_manual_commands_without_reserving_cooldown(self):
        for command, state in (('start', 'exited'), ('stop', 'running'), ('restart', 'running')):
            with self.subTest(command=command):
                obj = legacy.container()
                obj.id, obj.status = ID, state
                obj.attrs['Config'] = {'Labels': {'monit-docker.protected': 'true'}}
                engine, _, client = self.make_engine(obj)
                claim = Mock(return_value=True)
                with self.assertRaisesRegex(ActionRejected, 'container_protected'):
                    engine.run_manual_action(ID, command, claim)
                getattr(obj, command).assert_not_called()
                claim.assert_not_called()
                client.api.close.assert_called_once_with()
                self.assertIsNone(engine.collector.client)
                obj.attrs['Config']['Labels']['monit-docker.protected'] = 'false'
                engine.run_manual_action(ID, command, claim)
                getattr(obj, command).assert_called_once_with()

    def test_protected_containers_keep_metrics_and_autonomous_rules(self):
        obj = legacy.container()
        obj.attrs['Config'] = {'Labels': {'monit-docker.protected': 'true'}}
        engine, _, _ = self.make_engine(obj)
        result = engine.run_once()
        self.assertEqual(result.snapshots[0].cpu_percent, 256)
        self.assertTrue(result.snapshots[0].manual_actions_protected)
        rule = engine_tests.RuleParser().parse('status == running ? restart')
        result = engine.run_once(rules=(rule,))
        self.assertTrue(result.snapshots[0].manual_actions_protected)
        obj.restart.assert_called_once_with()
        self.assertEqual(len(result.actions), 1)

    def test_queued_request_rechecks_protection_from_fresh_selection(self):
        obj = legacy.container()
        obj.id = ID
        engine, _, _ = self.make_engine(obj)
        claim = Mock(return_value=True)
        actions = ManualActions(lambda identifier, action: engine.run_manual_action(identifier, action, claim),
                                ORIGIN, TOKEN)
        monitor = MonitorService(lambda _: engine.run_once(resources=('status',)), manual_actions=actions)
        monitor.run_cycle()
        actions.submit(payload(), monitor.status())
        obj.attrs['Config'] = {'Labels': {'monit-docker.protected': 'true'}}
        self.assertTrue(monitor.run_pending_action())
        record = actions.status()['recent'][0]
        self.assertEqual((record['status'], record['error']), ('failed', 'container_protected'))
        obj.restart.assert_not_called()
        claim.assert_not_called()
        monitor.run_cycle()
        self.assertTrue(monitor.status()['containers'][0]['manual_actions_protected'])

    def test_policy_metadata_is_boolean_and_not_a_metric(self):
        self.assertFalse(ContainerSnapshot().manual_actions_protected)
        for value in ('true', 1, None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                ContainerSnapshot(manual_actions_protected=value)
        original = ContainerSnapshot(manual_actions_protected=True)
        self.assertTrue(ContainerSnapshot(**original.to_dict()).manual_actions_protected)
        with self.assertRaises(AttributeError):
            original.manual_actions_protected = False
        engine, factory, _ = self.make_engine(legacy.container())
        with self.assertRaises(ValueError):
            engine.run_once(resources=('manual_actions_protected',))
        factory.assert_not_called()

    def test_reselects_exact_id_and_checks_status_before_reservation(self):
        obj = legacy.container('web')
        obj.id = ID
        engine, _, client = self.make_engine(obj)
        claim = Mock(return_value=True)
        engine.run_manual_action(ID, 'restart', claim)
        obj.restart.assert_called_once_with()
        claim.assert_called_once_with(ID)
        claim.reset_mock()
        replacement = legacy.container('web')
        replacement.id = 'c' * 64
        client.containers.list.return_value = [replacement]
        with self.assertRaisesRegex(ActionRejected, 'not_selected'):
            engine.run_manual_action(ID, 'restart', claim)
        client.containers.list.return_value = [obj]
        obj.status = 'exited'
        with self.assertRaisesRegex(ActionRejected, 'state_changed'):
            engine.run_manual_action(ID, 'restart', claim)
        claim.assert_not_called()
        engine.run_manual_action(ID, 'start', claim)
        obj.start.assert_called_once_with()
        self.assertIsNone(engine.collector.client)

    def test_unsupported_busy_and_cooldown_never_execute(self):
        obj = legacy.container()
        obj.id = ID
        engine, factory, _ = self.make_engine(obj)
        for command in ('exec', 'remove', 'kill', '@alias'):
            with self.assertRaises(ActionRejected):
                engine.run_manual_action(ID, command, Mock())
        factory.assert_not_called()
        engine._cycle_lock.acquire()
        try:
            with self.assertRaisesRegex(ActionRejected, 'busy'):
                engine.run_manual_action(ID, 'stop', Mock())
        finally:
            engine._cycle_lock.release()
        with self.assertRaisesRegex(ActionRejected, 'cooldown'):
            engine.run_manual_action(ID, 'stop', Mock(return_value=False))
        obj.stop.assert_not_called()
        engine.run_manual_action(ID, 'stop', Mock(return_value=True))
        obj.stop.assert_called_once_with()


class ManualHttpTests(unittest.TestCase):
    def setUp(self):
        self.actions = ManualActions(Mock(), ORIGIN, TOKEN)
        self.monitor = MonitorService(Mock(return_value=snapshot()), manual_actions=self.actions)
        self.monitor.run_cycle()
        self.server = StatusServer(('127.0.0.1', 0), self.monitor)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()

    def request(self, body=None, headers=None, path='/v1/actions', method='POST'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        defaults = {'Content-Type': 'application/json', 'Origin': ORIGIN, 'X-Monit-Action-Token': TOKEN}
        defaults.update(headers or {})
        defaults = {key: value for key, value in defaults.items() if value is not None}
        try:
            connection.request(method, path, body if body is not None else json.dumps(payload()), defaults)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_proxy_secret_and_exact_origin_required_before_queueing(self):
        for headers in ({'X-Monit-Action-Token': None}, {'X-Monit-Action-Token': 'wrong'},
                        {'Origin': None}, {'Origin': 'null'}, {'Origin': ORIGIN + '.evil'},
                        {'Origin': 'http://monitor.example'}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        self.assertEqual(self.actions.status()['recent'], [])
        code, accepted = self.request()
        self.assertEqual((code, accepted['status']), (202, 'queued'))
        self.actions.execute.assert_not_called()
        self.monitor.run_pending_action()
        self.actions.execute.assert_called_once_with(ID, 'restart')

    def test_authenticated_direct_api_cannot_bypass_protection(self):
        self.monitor.cycle.return_value = snapshot(manual_actions_protected=True)
        self.monitor.run_cycle()
        status_code, status = self.request(path='/v1/status', method='GET')
        self.assertEqual(status_code, 200)
        self.assertTrue(status['containers'][0]['manual_actions_protected'])
        for action in ('start', 'stop', 'restart', 'restart-reset'):
            with self.subTest(action=action):
                code, body = self.request(body=json.dumps(payload(action=action)))
                self.assertEqual((code, body), (403, {'error': 'container_protected'}))
        self.assertEqual(self.actions.status()['recent'], [])
        self.actions.execute.assert_not_called()

    def test_strict_json_framing_and_post_only(self):
        self.assertEqual(self.request(headers={'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request(body='x' * 1025)[0], 413)
        self.assertEqual(self.request(body='{')[0], 400)
        self.assertEqual(self.request(body='[]')[0], 400)
        self.assertEqual(self.request(headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.request(headers={'Content-Length': '9' * 5000})[0], 400)
        self.assertEqual(self.request(method='GET')[0], 404)
        self.assertEqual(self.request(method='PUT')[0], 405)
        self.assertEqual(self.request(path='/v1/actions/')[0], 405)
        self.assertEqual(self.actions.status()['recent'], [])
        self.monitor.manual_actions = None
        self.assertEqual(self.request()[0], 405)

    def test_rearm_uses_the_same_authenticated_endpoint(self):
        self.monitor.cycle.return_value = snapshot(restart_attempts=3, restart_limit=3)
        self.monitor.run_cycle()
        body = json.dumps(payload(action='restart-reset'))
        for headers in ({'X-Monit-Action-Token': None}, {'X-Monit-Action-Token': 'wrong'},
                        {'Origin': 'https://untrusted.example'}):
            self.assertEqual(self.request(body=body, headers=headers)[0], 403)
        self.assertEqual(self.actions.status()['recent'], [])
        self.assertEqual(self.request(body=body)[0], 202)
        self.monitor.run_pending_action()
        self.actions.execute.assert_called_once_with(ID, 'restart-reset')


class ManualCliTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    def options(self):
        token = Path(self.temp.name) / 'token'
        token.write_text(TOKEN + '\n')
        return ('serve', '--rsc', 'status', '--allow-actions', '--action-origin', ORIGIN,
                '--action-token-file', str(token), '--state-file', str(Path(self.temp.name) / 'state.json'))

    def test_opt_in_secret_and_origin_validation_before_server(self):
        for args in (('serve', '--allow-actions'), ('serve', '--action-origin', ORIGIN),
                     self.options() + ('--action-origin', 'http://monitor.example'),
                     self.options() + ('--action-origin', ORIGIN + '/'),
                     self.options() + ('--action-cooldown', '0'),
                     self.options() + ('--dry-run', '--cmd', 'restart')):
            with self.subTest(args=args), patch('monit_docker.adapters.http.run_server') as server:
                with self.assertRaises(SystemExit):
                    self.invoke(*args)
                server.assert_not_called()
        options = self.options()
        (Path(self.temp.name) / 'token').write_text('short')
        with patch('monit_docker.adapters.http.run_server') as server:
            self.assertEqual(self.invoke(*options), 110)
            server.assert_not_called()

    def test_manual_cooldown_persists_across_service_instances_and_all_commands(self):
        obj = self.client.containers.list.return_value[0]
        obj.id = ID
        statuses = []
        def run(monitor, *args):
            monitor.run_cycle()
            monitor.manual_actions.submit(payload(len(statuses) + 1, action='stop'), monitor.status())
            monitor.run_pending_action()
            statuses.append(monitor.status()['manual_actions']['recent'][0])
        with patch('monit_docker.adapters.http.run_server', side_effect=run):
            self.assertEqual(self.invoke(*self.options()), 0)
            self.assertEqual(self.invoke(*self.options()), 0)
        self.assertEqual([row['status'] for row in statuses], ['succeeded', 'failed'])
        self.assertEqual(statuses[1]['error'], 'cooldown')
        obj.stop.assert_called_once_with()
