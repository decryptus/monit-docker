"""Cycles and failure boundaries exercised through the actual engine/adapters."""

import json
import unittest
from unittest.mock import Mock

from docker.errors import APIError, DockerException, NotFound

from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core import MonitoringEngine
from monit_docker.domain.errors import CommandExecutionError, MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult

from test_monit_docker import container


class Stream(object):
    def __init__(self, lines, error=None, close_error=None):
        self.lines = lines
        self.error = error
        self.close_error = close_error
        self.closed = False

    def __iter__(self):
        for line in self.lines:
            yield line
        if self.error:
            raise self.error

    def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class EngineTests(unittest.TestCase):
    def make_engine(self, *objects, **kwargs):
        client = Mock()
        client.containers.list.return_value = list(objects)
        factory = Mock(return_value=client)
        collector = DockerCollector(factory, kwargs.get('selector'))
        engine = MonitoringEngine(collector, DockerActionExecutor(collector))
        return engine, factory, client

    def assert_code(self, code, function, *args, **kwargs):
        with self.assertRaises(MonitoringError) as error:
            function(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def test_repeated_cycles_replace_objects_and_cpu_baseline_even_for_same_id(self):
        first, second = container(cpu=256), container(cpu=70)
        engine, factory, client = self.make_engine(first)
        rules = (RuleParser().parse('cpu_percent > 100 ? restart'),)
        initial = engine.run_once(rules=rules)
        client.containers.list.return_value = [second]
        following = engine.run_once(rules=rules)
        self.assertEqual(initial.snapshots[0].cpu_percent, 256)
        self.assertEqual(following.snapshots[0].cpu_percent, 70)
        self.assertEqual(len(initial.actions), 1)
        self.assertEqual(following.actions, ())
        first.restart.assert_called_once_with()
        second.restart.assert_not_called()
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(client.api.close.call_count, 2)
        self.assertIsNone(engine.collector.client)
        self.assertEqual(engine.collector._containers, {})

    def test_disappeared_container_is_not_acted_on_in_next_cycle(self):
        first, replacement = container('old'), container('replacement')
        engine, _, client = self.make_engine(first)
        rules = (RuleParser().parse('restart'),)
        engine.run_once(rules=rules)
        client.containers.list.return_value = [replacement]
        result = engine.run_once(rules=rules)
        self.assertEqual([s.id for s in result.snapshots], ['replacement'])
        first.restart.assert_called_once_with()
        replacement.restart.assert_called_once_with()

    def test_no_containers_closes_cycle_and_allows_next_call(self):
        engine, _, client = self.make_engine()
        self.assert_code(114, engine.run_once)
        client.api.close.assert_called_once_with()
        client.containers.list.return_value = [container()]
        self.assertEqual(len(engine.run_once().snapshots), 1)

    def test_stream_is_closed_on_success_and_duplicate_timestamps_skipped(self):
        obj = container()
        lines = list(obj.stats.return_value)
        stream = Stream([lines[0], lines[0], lines[1]])
        obj.stats.return_value = stream
        engine, _, client = self.make_engine(obj)
        result = engine.run_once(resources=('cpu_percent',))
        self.assertEqual(result.snapshots[0].cpu_percent, 256)
        self.assertTrue(stream.closed)
        client.api.close.assert_called_once_with()

    def test_incomplete_stream_reports_115_and_closes(self):
        for lines in ([], [b'{"read":"same"}', b'{"read":"same"}']):
            with self.subTest(lines=lines):
                obj = container()
                stream = Stream(lines)
                obj.stats.return_value = stream
                engine, _, client = self.make_engine(obj)
                self.assert_code(115, engine.run_once)
                self.assertTrue(stream.closed)
                client.api.close.assert_called_once_with()

    def test_sampling_and_stream_cleanup_failure_preserves_original_error(self):
        obj = container()
        stream = Stream([], error=NotFound('container vanished'), close_error=RuntimeError('close'))
        obj.stats.return_value = stream
        engine, _, client = self.make_engine(obj)
        client.api.close.side_effect = RuntimeError('client close')
        with self.assertLogs('monit-docker', level='ERROR'), self.assertRaises(NotFound):
            engine.run_once()
        self.assertTrue(stream.closed)
        self.assertIsNone(engine.collector.client)

    def test_invalid_json_closes_stream_and_client(self):
        obj = container()
        stream = Stream([b'not json'])
        obj.stats.return_value = stream
        engine, _, client = self.make_engine(obj)
        with self.assertRaises(ValueError):
            engine.run_once()
        self.assertTrue(stream.closed)
        client.api.close.assert_called_once_with()

    def test_stream_close_error_is_reported_if_sampling_succeeded(self):
        obj = container()
        stream = Stream(list(obj.stats.return_value), close_error=RuntimeError('close'))
        obj.stats.return_value = stream
        engine, _, client = self.make_engine(obj)
        with self.assertRaisesRegex(RuntimeError, 'close'):
            engine.run_once()
        client.api.close.assert_called_once_with()

    def test_list_failure_closes_client_and_next_cycle_recovers(self):
        obj = container()
        engine, _, client = self.make_engine(obj)
        client.containers.list.side_effect = APIError('list failed')
        with self.assertRaises(APIError):
            engine.run_once()
        client.api.close.assert_called_once_with()
        client.containers.list.side_effect = None
        self.assertEqual(engine.run_once().snapshots[0].id, obj.id)

    def test_factory_failure_releases_cycle_lock(self):
        engine, factory, client = self.make_engine(container())
        factory.side_effect = DockerException('connect failed')
        with self.assertRaises(DockerException):
            engine.run_once()
        client.api.close.assert_not_called()
        factory.side_effect = None
        self.assertEqual(len(engine.run_once().snapshots), 1)

    def test_cleanup_error_after_success_releases_lock_and_connection(self):
        engine, _, client = self.make_engine(container())
        client.api.close.side_effect = RuntimeError('close')
        with self.assertRaisesRegex(RuntimeError, 'close'):
            engine.run_once(resources=('status',))
        client.api.close.side_effect = None
        self.assertEqual(len(engine.run_once(resources=('status',)).snapshots), 1)

    def test_callback_can_exit_early_but_connection_is_closed(self):
        first, second = container('first'), container('second')
        engine, _, client = self.make_engine(first, second)
        callback = Mock(side_effect=SystemExit(70))
        with self.assertRaises(SystemExit) as error:
            engine.run_once(resources=('cpu_percent',), on_snapshot=callback)
        self.assertEqual(error.exception.code, 70)
        second.stats.assert_not_called()
        client.api.close.assert_called_once_with()

    def test_nested_cycle_rejected_without_closing_outer_cycle(self):
        engine, factory, client = self.make_engine(container())

        def observe(snapshot):
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                engine.run_once()
            client.api.close.assert_not_called()
        engine.run_once(resources=('status',), on_snapshot=observe)
        factory.assert_called_once_with()
        client.api.close.assert_called_once_with()

    def test_action_failure_stops_all_later_actions_and_containers(self):
        first, second = container('first'), container('second')
        first.pause.side_effect = APIError('gone')
        engine, _, client = self.make_engine(first, second)
        parser = RuleParser()
        rules = tuple(parser.parse(s) for s in ('restart', 'mem_percent > 60 ? pause', 'mem_percent > 60 ? kill'))
        self.assert_code(116, engine.run_once, rules=rules)
        first.restart.assert_called_once_with()
        first.kill.assert_not_called()
        second.restart.assert_not_called()
        client.api.close.assert_called_once_with()

    def test_engine_preserves_exec_status_without_changing_agent_error_code(self):
        from docker.models.containers import ExecResult
        obj = container()
        obj.exec_run.return_value = ExecResult(42, b'failed')
        engine, _, client = self.make_engine(obj)
        with self.assertRaises(CommandExecutionError) as error:
            engine.run_once(rules=(RuleParser().parse('(probe)'),))
        self.assertEqual(error.exception.code, 116)
        self.assertEqual(error.exception.exit_code, 42)
        client.api.close.assert_called_once_with()

    def test_metadata_rules_run_before_metrics_regardless_of_argument_order(self):
        obj = container()
        engine, _, _ = self.make_engine(obj)
        parser = RuleParser()
        rules = tuple(parser.parse(s) for s in ('mem_percent > 60 ? pause', 'restart'))
        result = engine.run_once(rules=rules)
        self.assertEqual([r.command for r in result.actions], ['restart', 'pause'])

    def test_status_changes_from_reload_visible_to_next_rule(self):
        obj = container()
        obj.reload.side_effect = lambda: setattr(obj, 'status', 'paused')
        engine, _, _ = self.make_engine(obj)
        parser = RuleParser()
        result = engine.run_once(rules=tuple(parser.parse(s) for s in
                                             ('reload', 'status == paused ? unpause')))
        self.assertEqual([r.command for r in result.actions], ['reload', 'unpause'])
        obj.stats.assert_not_called()

    def test_stopped_container_skips_metrics_but_accepts_status_actions(self):
        obj = container()
        obj.status = 'exited'
        engine, _, _ = self.make_engine(obj)
        parser = RuleParser()
        result = engine.run_once(rules=tuple(parser.parse(s) for s in
                         ('status == exited ? start', 'mem_percent > 60 ? restart')))
        obj.start.assert_called_once_with()
        obj.restart.assert_not_called()
        obj.stats.assert_not_called()
        self.assertIsNone(result.snapshots[0].mem_percent)

    def test_metadata_only_collection_does_not_open_a_stats_stream(self):
        obj = container()
        engine, _, _ = self.make_engine(obj)
        result = engine.run_once(resources=('pid', 'status'))
        self.assertEqual(result.snapshots[0].pid, 123)
        obj.stats.assert_not_called()

    def test_requested_metrics_are_collected_alongside_metadata_only_rules(self):
        obj = container()
        engine, _, _ = self.make_engine(obj)
        result = engine.run_once(rules=(RuleParser().parse('restart'),), resources=('mem_percent',))
        self.assertEqual(result.snapshots[0].mem_percent, 80)
        obj.restart.assert_called_once_with()

    def test_engine_operates_on_plain_snapshots_without_docker_adapter(self):
        snapshot = ContainerSnapshot(id='one', name='one', status='running', pid=1)
        collector = Mock()
        collector.select.return_value = (snapshot,)
        collector.describe.return_value = snapshot
        executor = Mock()
        executor.execute.return_value = True
        engine = MonitoringEngine(collector, executor)
        rule = RuleParser().parse('status == running ? restart')
        result = engine.run_once(rules=(rule,))
        self.assertIsInstance(result, CycleResult)
        executor.execute.assert_called_once_with('one', rule.actions[0])
        collector.collect.assert_not_called()
        collector.end_cycle.assert_called_once_with()

    def test_selection_filters_and_short_id_work_without_stale_groups(self):
        web, db = container('web'), container('db')
        web.id = '123456789012' + 'a' * 52
        selector = ContainerSelector(selectors={'id': ['123456789012']})
        engine, _, _ = self.make_engine(web, db, selector=selector)
        result = engine.run_once(resources=('status',))
        self.assertEqual([s.name for s in result.snapshots], ['web'])
        web.stats.assert_not_called()
        db.stats.assert_not_called()


if __name__ == '__main__':
    unittest.main()
