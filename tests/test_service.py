"""Cached monitoring and real loopback HTTP regression tests."""

import http.client
import json
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.http import StatusServer, run_server
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import ActionDecision, CycleResult
from monit_docker.outputs.prometheus import render_metrics
from monit_docker.service import MonitorService
from monit_docker import cli
import test_monit_docker as legacy


def cycle_result(name='web', identifier='abc'):
    return CycleResult((ContainerSnapshot(id=identifier, name=name, status='running',
                                         cpu_percent=230, mem_usage=1024, net_rx=42),), ())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=1000)
        self.ticks = Mock(return_value=10)
        self.cycle = Mock(return_value=cycle_result())
        self.monitor = MonitorService(self.cycle, 1, 3, self.clock, self.ticks)

    def test_startup_success_staleness_and_clock_independence(self):
        self.assertFalse(self.monitor.status()['ready'])
        self.monitor.run_cycle()
        self.assertTrue(self.monitor.status()['ready'])
        self.clock.return_value = -1000
        self.ticks.return_value = 13
        self.assertTrue(self.monitor.status()['ready'])
        self.ticks.return_value = 13.01
        stale = self.monitor.status()
        self.assertFalse(stale['ready'])
        self.assertEqual(stale['containers'], [])
        self.assertEqual(stale['last_success_at'], 1000)

    def test_failure_clears_data_and_next_cycle_recovers_without_leaking_error(self):
        self.monitor.run_cycle()
        self.cycle.side_effect = MonitoringError(170, 'private-token-value')
        with self.assertLogs('monit-docker', level='ERROR'):
            self.monitor.run_cycle()
        failed = self.monitor.status()
        self.assertFalse(failed['ready'])
        self.assertEqual(failed['containers'], [])
        self.assertEqual(failed['last_error_code'], 170)
        self.assertNotIn('private-token-value', json.dumps(failed))
        self.cycle.side_effect = None
        self.cycle.return_value = cycle_result('replacement', 'xyz')
        self.monitor.run_cycle()
        self.assertEqual(self.monitor.status()['cycles_total'], 3)
        self.assertEqual(self.monitor.status()['errors_total'], 1)
        self.assertEqual(self.monitor.status()['containers'][0]['id'], 'xyz')

    def test_cache_is_a_copy_and_partial_action_counts_survive_failure(self):
        def partial(observer):
            observer(ActionDecision('abc', 'secret command', 'secret command', 'executed'))
            raise MonitoringError(116, 'failed')
        self.cycle.side_effect = partial
        with self.assertLogs('monit-docker', level='ERROR'):
            self.monitor.run_cycle()
        data = self.monitor.status()
        self.assertEqual(data['actions']['executed'], 1)
        self.assertNotIn('secret command', json.dumps(data))
        data['actions']['executed'] = 999
        self.assertEqual(self.monitor.status()['actions']['executed'], 1)

    def test_blocked_cycle_does_not_block_cache_and_rejects_overlap(self):
        entered, release = Event(), Event()
        def slow(observer):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test timeout')
            return cycle_result()
        self.monitor.run_cycle()
        self.cycle.side_effect = slow
        thread = Thread(target=self.monitor.run_cycle)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.ticks.return_value = 20
            self.assertTrue(self.monitor.status()['running'])
            self.assertFalse(self.monitor.status()['ready'])
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                self.monitor.run_cycle()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_schedule_waits_after_completion_and_stop_prevents_next_cycle(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        self.monitor.run(stop)
        self.assertEqual(self.cycle.call_count, 2)
        self.assertEqual([c.args for c in stop.wait.call_args_list], [(1,), (1,)])
        stop.is_set.return_value = True
        self.monitor.run(stop)
        self.assertEqual(self.cycle.call_count, 2)

    def test_metrics_escape_labels_preserve_units_and_omit_unknown_values(self):
        self.cycle.return_value = cycle_result('a"b\\c\nd')
        self.monitor.run_cycle()
        rendered = render_metrics(self.monitor.status())
        self.assertIn('name="a\\"b\\\\c\\nd"', rendered)
        self.assertIn('monit_docker_container_cpu_usage_percent{id="abc",name="a\\"b\\\\c\\nd"} 230\n', rendered)
        self.assertIn('# TYPE monit_docker_container_network_receive_bytes_total counter\n', rendered)
        self.assertNotIn('monit_docker_container_memory_limit_bytes{', rendered)
        self.assertTrue(rendered.endswith('\n'))


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.cycle = Mock(return_value=cycle_result())
        self.monitor = MonitorService(self.cycle)
        self.server = StatusServer(('127.0.0.1', 0), self.monitor)
        self.thread = Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.01})
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()

    def request(self, path, method='GET'):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_health_readiness_and_endpoints_never_invoke_a_cycle(self):
        self.assertEqual(self.request('/healthz')[0], 200)
        self.assertEqual(self.request('/readyz')[0], 503)
        self.assertFalse(json.loads(self.request('/v1/status')[2])['ready'])
        self.assertEqual(self.request('/metrics')[0], 200)
        self.cycle.assert_not_called()
        self.monitor.run_cycle()
        self.assertEqual(self.request('/readyz')[0], 200)
        for _ in range(3):
            self.assertEqual(self.request('/metrics')[1]['Content-Type'],
                             'text/plain; version=0.0.4; charset=utf-8')
            self.assertEqual(json.loads(self.request('/v1/status')[2])['containers'][0]['id'], 'abc')
        self.cycle.assert_called_once()

    def test_http_methods_routes_and_head(self):
        self.assertEqual(self.request('/missing')[0], 404)
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            code, headers, _ = self.request('/v1/status', method)
            self.assertEqual(code, 405)
            self.assertEqual(headers['Allow'], 'GET, HEAD')
        code, headers, body = self.request('/healthz', 'HEAD')
        self.assertEqual((code, body), (200, b''))
        self.assertGreater(int(headers['Content-Length']), 0)
        self.cycle.assert_not_called()

    def test_requests_remain_responsive_during_blocked_cycle(self):
        entered, release = Event(), Event()
        self.cycle.side_effect = lambda observer: (entered.set(), release.wait(5), cycle_result())[-1]
        worker = Thread(target=self.monitor.run_cycle)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('/healthz')[0], 200)
            self.assertEqual(self.request('/readyz')[0], 503)
        finally:
            release.set()
            worker.join(5)

    def test_busy_port_fails_before_monitoring_and_shutdown_releases_port(self):
        with self.assertRaises(OSError):
            run_server(self.monitor, *self.server.server_address)
        self.cycle.assert_not_called()
        monitor = Mock()
        with patch('monit_docker.adapters.http.signal.signal') as signal:
            run_server(monitor, '127.0.0.1', 0)
        monitor.run.assert_called_once()
        self.assertEqual(signal.call_count, 4)  # Install and restore SIGINT/SIGTERM.


class ServeCliTests(unittest.TestCase):
    setUp = legacy.RegressionTests.setUp
    invoke = legacy.RegressionTests.invoke

    def test_defaults_are_loopback_read_only_and_collect_all_resources(self):
        def run(monitor, bind, port):
            self.assertEqual((bind, port), ('127.0.0.1', 9808))
            monitor.run_cycle()
            self.assertTrue(monitor.status()['ready'])
            self.assertEqual(monitor.status()['containers'][0]['mem_percent'], 80)
        with patch('monit_docker.adapters.http.run_server', side_effect=run):
            self.assertEqual(self.invoke('serve'), 0)
        self.client.containers.list.return_value[0].restart.assert_not_called()

    def test_rules_reuse_cron_state_across_cycles_and_dry_run_does_not_persist(self):
        from pathlib import Path
        path = Path(self.temp.name) / 'serve.json'
        obj = self.client.containers.list.return_value[0]
        def run(monitor, *args):
            monitor.run_cycle()
            monitor.run_cycle()
            self.assertTrue(monitor.status()['ready'])
            self.assertEqual(monitor.status()['actions']['executed'], 1)
            self.assertEqual(monitor.status()['actions']['cooldown'], 1)
        with patch('monit_docker.adapters.http.run_server', side_effect=run):
            self.assertEqual(self.invoke('serve', '--rsc', 'status', '--state-file', str(path), '--cmd', 'restart'), 0)
        obj.restart.assert_called_once_with()
        def preview(monitor, *args):
            monitor.run_cycle()
            monitor.run_cycle()
            self.assertEqual(monitor.status()['actions']['dry-run'], 2)
        path.unlink()
        with patch('monit_docker.adapters.http.run_server', side_effect=preview):
            self.assertEqual(self.invoke('serve', '--rsc', 'status', '--state-file', str(path), '--dry-run', '--cmd', 'restart'), 0)
        self.assertFalse(path.exists())
        obj.restart.assert_called_once_with()

    def test_invalid_options_never_start_server(self):
        cases = [('--cmd', 'restart'), ('--dry-run',), ('--interval', 'nan'),
                 ('--interval', '0'), ('--port', '0'), ('--port', '65536'),
                 ('--bind', 'bad'), ('--stale-after', '1'), ('--cooldown', '-1')]
        with patch('monit_docker.adapters.http.run_server') as server:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(SystemExit) as error:
                    self.invoke('serve', *args)
                self.assertEqual(error.exception.code, 2)
            server.assert_not_called()


if __name__ == '__main__':
    unittest.main()
