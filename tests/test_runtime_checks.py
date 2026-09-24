"""Runtime check boundaries, failure semantics, rules and public projections."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from monit_docker import cli
from monit_docker.adapters.docker import DockerCollector
from monit_docker.adapters.events import event_counts, read_history, collect_events
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.validation import check_configuration
from monit_docker.audit import AuditJournal
from monit_docker.core import MonitoringEngine, RuleEvaluator
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.outputs.prometheus import render_metrics
from test_monit_docker import container

NOW = 1000
WINDOW = 300
NANO = 1000000000


def event(action, stamp, identifier='demo', kind='container'):
    return dict(Type=kind, Action=action, Actor={'ID': identifier, 'Attributes': {'secret': 'never exposed'}},
                time=stamp, timeNano=stamp * NANO)


def response(events):
    result = Mock()
    result.iter_content.return_value = [(json.dumps(item) + '\n').encode() for item in events]
    return result


def collector_for(objects, events=()):
    client = Mock()
    client.containers.list.return_value = objects
    client.api.get.return_value = response(events)
    return DockerCollector(lambda: client), client


def pid_container(current=8, limit=10):
    obj = container()
    obj.attrs['HostConfig'] = {'PidsLimit': limit}
    samples = [dict(read=n, pids_stats={'current': current}) for n in (1, 2)]
    obj.stats.return_value = iter(json.dumps(item).encode() for item in samples)
    return obj


class EventTests(unittest.TestCase):
    def test_window_boundaries_duplicates_and_container_identity(self):
        events = [event('oom', 700), event('oom', 701), event('start', 999),
                  event('start', 999), event('oom', 1000), event('start', 950, 'replacement'),
                  event('restart', 999), event('die', 999), event('pull', 999, kind='image')]
        data = event_counts(events, ['demo', 'replacement'], WINDOW, NOW)
        self.assertEqual(data['demo']['oom_events'], 2)
        self.assertEqual(data['demo']['starts_recent'], 1)
        self.assertEqual(data['replacement']['starts_recent'], 1)
        self.assertEqual(data['replacement']['oom_events'], 0)
        self.assertNotIn('secret', json.dumps(data))

    def test_initial_start_is_counted_and_daemon_restart_event_not_double_counted(self):
        events = [event('create', 800), event('start', 801), event('die', 900),
                  event('start', 901), event('restart', 902)]
        self.assertEqual(event_counts(events, ['demo'], WINDOW, NOW)['demo']['starts_recent'], 2)

    def test_unrelated_events_can_exhaust_history_and_never_mean_zero(self):
        events = [event('pull', 800 + n // 2, kind='image') for n in range(256)]
        data = event_counts(events, ['demo'], WINDOW, NOW)['demo']
        self.assertEqual(data['event_history_complete'], 0)
        self.assertIsNone(data['oom_events'])
        self.assertIsNone(data['starts_recent'])
        events[0] = event('pull', 700, kind='image')
        self.assertEqual(event_counts(events, ['demo'], WINDOW, NOW)['demo']['event_history_complete'], 1)

    def test_invalid_events_and_future_timestamps_fail(self):
        invalid = [None, {}, event('oom', 1001), event('oom', -1),
                   dict(Type='container', Action='oom', timeNano=True),
                   dict(Type='container', Action=1, Actor={'ID': 'demo'}, timeNano=999*NANO)]
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                event_counts([item], ['demo'], WINDOW, NOW)

    def test_history_closes_response_and_rejects_truncation_oversize_and_timeout(self):
        api = Mock()
        for chunks in ([b'{'], [b'x' * 65537], [b'{}\n'] * 257):
            result = response([])
            result.iter_content.return_value = chunks
            api.get.return_value = result
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                read_history(api, NOW)
            result.close.assert_called_once()
        result = response([])
        result.iter_content.side_effect = TimeoutError()
        api.get.return_value = result
        with self.assertRaises(MonitoringError) as raised:
            collect_events(api, ['demo'], WINDOW, now=NOW+1)
        self.assertEqual(raised.exception.code, 115)
        result.close.assert_called_once()
        self.assertEqual(api.get.call_args.kwargs['timeout'], 5)
        self.assertEqual(api.get.call_args.kwargs['params'], {'since': '1', 'until': '1000'})

    def test_one_query_for_all_selected_containers_and_no_stats_for_event_rules(self):
        one, two = container('one'), container('two')
        collector, client = collector_for([one, two], [event('oom', 999, 'one')])
        executor = Mock()
        engine = MonitoringEngine(collector, executor)
        rule = RuleParser().parse('oom_events > 0 ? restart')
        with patch('monit_docker.adapters.events.time.time', return_value=NOW+1):
            result = engine.run_once(rules=(rule,))
        self.assertEqual(len(result.actions), 1)
        client.api.get.assert_called_once()
        one.stats.assert_not_called()
        two.stats.assert_not_called()
        client.api.exec_create.assert_not_called()

    def test_event_rules_work_on_stopped_containers_and_are_audited(self):
        obj = container(); obj.status = 'exited'
        collector, client = collector_for([obj], [event('oom', 999)])
        with tempfile.TemporaryDirectory() as directory:
            journal = AuditJournal(Path(directory) / 'audit.jsonl', emit=False)
            engine = MonitoringEngine(collector, Mock(), audit=journal)
            with patch('monit_docker.adapters.events.time.time', return_value=NOW+1):
                engine.run_once(rules=(RuleParser().parse('oom_events > 0 ? start'),))
            self.assertEqual([r['event'] for r in journal.read()], ['started', 'completed'])
            self.assertEqual(journal.read()[-1]['result'], 'succeeded')

    def test_truncated_history_cannot_trigger_zero_comparison_or_actions(self):
        collector, client = collector_for([container()], [event('pull', 999, kind='image')] * 256)
        executor = Mock()
        with patch('monit_docker.adapters.events.time.time', return_value=NOW+1):
            with self.assertRaises(MonitoringError) as raised:
                MonitoringEngine(collector, executor).run_once(rules=(RuleParser().parse('oom_events == 0 ? restart'),))
        self.assertEqual(raised.exception.code, 115)
        executor.execute.assert_not_called()

    def test_opt_in_and_no_stale_events_after_later_cycles(self):
        collector, client = collector_for([container()], [event('oom', 999)])
        engine = MonitoringEngine(collector, Mock())
        engine.run_once(resources=('status',))
        client.api.get.assert_not_called()
        with patch('monit_docker.adapters.events.time.time', return_value=NOW+1):
            self.assertEqual(engine.run_once(resources=('oom_events',)).snapshots[0].oom_events, 1)
        self.assertIsNone(engine.run_once(resources=('status',)).snapshots[0].oom_events)

    def test_manual_action_does_not_inherit_previous_event_probe(self):
        collector, client = collector_for([container()], [event('oom', 999)])
        engine = MonitoringEngine(collector, Mock())
        with patch('monit_docker.adapters.events.time.time', return_value=NOW+1):
            engine.run_once(resources=('oom_events',))
        client.api.get.reset_mock(side_effect=True)
        client.api.get.side_effect = TimeoutError('events unavailable')
        engine.run_manual_action('demo', 'restart', lambda identifier: True)
        client.api.get.assert_not_called()


class PidTests(unittest.TestCase):
    def test_counts_include_threads_and_percentage_uses_configured_limit(self):
        collector, client = collector_for([pid_container()])
        item = MonitoringEngine(collector, Mock()).run_once(resources=('pids_percent',)).snapshots[0]
        self.assertEqual((item.pids_current, item.pids_limit, item.pids_percent), (8, 10, 80.0))
        client.api.get.assert_not_called()
        client.api.exec_create.assert_not_called()

    def test_missing_or_unlimited_limit_does_not_manufacture_a_percentage(self):
        for limit in (None, 0, -1):
            collector, _ = collector_for([pid_container(limit=limit)])
            item = MonitoringEngine(collector, Mock()).run_once(resources=('pids_current',)).snapshots[0]
            self.assertEqual(item.pids_current, 8)
            self.assertIsNone(item.pids_limit)
            self.assertIsNone(item.pids_percent)
            with self.assertRaises(MonitoringError):
                RuleEvaluator().matches(RuleParser().parse('pids_percent > 80 ? restart'), item)

    def test_invalid_or_missing_pid_accounting_is_an_error(self):
        for value in (None, True, -1, '8', 2.5):
            collector, _ = collector_for([pid_container(current=value)])
            with self.subTest(value=value), self.assertRaises(MonitoringError):
                MonitoringEngine(collector, Mock()).run_once(resources=('pids_current',))

    def test_numeric_rule_validation_and_offline_checks(self):
        for expression in ('oom_events == 1.5', 'starts_recent in (running)', 'pids_current > 2 MiB',
                           'pids_limit == unlimited', 'pids_percent > healthy'):
            with self.subTest(expression=expression), self.assertRaises(RuleSyntaxError):
                RuleParser().parse(expression + ' ? restart')
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / 'config.yml'; conf.write_text('{}')
            check_configuration(str(conf), expressions=['oom_events > 0 ? stop',
                                  'starts_recent >= 3 ? stop', 'pids_percent > 80.5 ? restart'])

    def test_prometheus_omits_unknown_counts_and_stale_data(self):
        snapshot = ContainerSnapshot(id='demo', name='demo', status='running', pids_current=8,
                                     event_window_seconds=300, event_window_end=1000,
                                     event_history_complete=0)
        data = dict(ready=True, running=False, cycles_total=1, errors_total=0,
                    last_success_at=None, actions={}, containers=[snapshot.to_dict()])
        metrics = render_metrics(data)
        self.assertIn('monit_docker_container_pids_current{id="demo",name="demo"} 8', metrics)
        self.assertNotIn('monit_docker_container_oom_events{', metrics)
        self.assertNotIn('monit_docker_container_pids_percent{', metrics)
        data['ready'] = False
        self.assertNotIn('monit_docker_container_pids_current{', render_metrics(data))

    def test_cli_window_validation_and_read_only_output(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ['monit-docker', '-c', directory + '/absent', '--logfile', directory + '/missing/log', '--runtimedir', '']
            for window in ('0', '-1', '86401', 'nan'):
                with patch('sys.argv', base + ['--event-window', window, 'stats']), patch('sys.stderr', new_callable=io.StringIO):
                    with self.assertRaises(SystemExit): cli.argv_parse_check()
            obj = pid_container()
            collector, client = collector_for([obj])
            with patch.object(cli, 'MONIT_DOCKER_CONFIG', None), patch('docker.from_env', return_value=client), \
                    patch('sys.argv', base + ['stats', '--rsc', 'pids_current']), patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(cli.main(cli.argv_parse_check()), 0)
                self.assertEqual(json.loads(output.getvalue()), {'demo': {'pids_current': 8}})


if __name__ == '__main__':
    unittest.main()
