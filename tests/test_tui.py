"""Terminal isolation, read-only collection, failure and lifecycle coverage."""
import curses
import importlib.abc
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from monit_docker import cli, tui
from monit_docker.audit_query import QueryError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.observation import Observation


def result():
    return SimpleNamespace(snapshots=[ContainerSnapshot(id='a' * 64, name='web', status='running')])


def test_cli_cron_never_load_terminal():
    code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('curses', 'monit_docker.tui', 'dwho.tui'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from monit_docker.cli import argv_parse_check, main
from unittest.mock import patch
assert argv_parse_check(['stats']).subcommand == 'stats'
assert argv_parse_check(['cron', '--state-file', '/tmp/state', '--cmd', 'restart']).subcommand == 'cron'
with patch('monit_docker.cli.MonitDockerSubCmdStats.__call__', return_value=0), patch('monit_docker.cli.initialize_runtime'):
    assert main(argv_parse_check(['stats'])) == 0
with patch('monit_docker.cli.MonitDockerSubCmdCron.__call__', return_value=0), patch('monit_docker.cli.initialize_runtime'):
    assert main(argv_parse_check(['cron', '--state-file', '/tmp/state', '--cmd', 'restart'])) == 0
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_piped_tui_rejects_before_client_or_curses(capsys):
    with patch('monit_docker.cli.build_application', side_effect=AssertionError), patch.object(tui.sys.stdin, 'isatty', return_value=False):
        assert cli.main(cli.argv_parse_check(['tui'])) == 2
    assert 'interactive terminal' in capsys.readouterr().err


def test_tui_only_composes_stats_without_rules():
    app = Mock()
    with patch('monit_docker.cli.build_application', return_value=app) as build, patch('monit_docker.tui.run', side_effect=lambda factory: factory()) :
        observation = cli.MonitDockerSubCmdTui(cli.argv_parse_check(['--name', 'web', 'tui']))()
    job = build.call_args.args[0]
    assert job.subcommand == 'stats' and job.name == ['web']
    assert not job.cmd and not job.state_file and not job.allow_actions
    assert build.call_args.kwargs['use_rules'] is False
    assert observation.collect == app.run_once
    assert observation.reader is None


def test_failure_clears_old_measurements_and_recovers():
    collect = Mock(side_effect=[result(), OSError(), result()])
    clock = Mock(return_value=0)
    observation = Observation(collect, clock=clock)
    observation.refresh()
    assert observation.snapshot()['containers']
    observation.refresh()
    assert not observation.snapshot()['containers']
    assert observation.snapshot()['collection_error'] == 'OSError'
    observation.refresh()
    assert observation.snapshot()['collection_error'] is None
    clock.return_value = 100
    assert observation.snapshot()['stale']
    assert not observation.snapshot()['containers']


def test_journal_busy_is_not_successful_empty_history():
    reader = Mock()
    reader.page.side_effect = [QueryError('audit_busy'), dict(records=[{'action': 'restart'}], next_cursor='more')]
    observation = Observation(result, reader)
    observation.refresh()
    assert observation.snapshot()['journal_error'] == 'audit_busy'
    observation.refresh()
    data = observation.snapshot()
    assert data['records'] and data['journal_more'] and data['journal_error'] is None
    assert reader.page.call_count == 2


def test_slow_collection_does_not_overlap_or_block_close():
    started, finish = Event(), Event()
    def collect():
        started.set()
        assert finish.wait(5)
        return result()
    wrapped = Mock(side_effect=collect)
    observation = Observation(wrapped, interval=5)
    observation.start()
    assert started.wait(2)
    observation.close()
    assert wrapped.call_count == 1
    finish.set()
    observation.worker.join(2)
    assert not observation.worker.is_alive()


class Screen:
    def __init__(self, keys, sizes=((24, 80),)):
        self.keys = iter(keys)
        self.sizes = iter(sizes)
        self.size = (24, 80)
        self.writes = []
        self.timeouts = []
    def keypad(self, value): pass
    def timeout(self, value): self.timeouts.append(value)
    def erase(self): pass
    def refresh(self): pass
    def getmaxyx(self):
        self.size = next(self.sizes, self.size)
        return self.size
    def addnstr(self, row, col, text, limit, style): self.writes.append(text[:limit])
    def get_wch(self): return next(self.keys)


def test_navigation_resize_and_dwho_details():
    observation = Observation(result)
    observation.refresh()
    screen = Screen([curses.KEY_RESIZE, curses.KEY_DOWN, '\n', '\t', 'q'], ((3, 10), (24, 80)))
    with patch.object(tui.sys.stdin, 'isatty', return_value=True), patch('curses.curs_set'), patch('dwho.tui.view_details') as details:
        assert tui.screen_loop(screen, observation) == 0
    details.assert_called_once()
    assert -1 in screen.timeouts and screen.timeouts[-1] == tui.POLL_MS
    assert any('Journal disabled' in text for text in screen.writes)


@pytest.mark.parametrize('error,expected', [(KeyboardInterrupt(), 130), (OSError(), 2), (curses.error(), 2)])
def test_terminal_restoration_and_worker_cleanup(error, expected):
    observation = Mock()
    with patch.object(tui.sys.stdin, 'isatty', return_value=True), patch.object(tui.sys.stdout, 'isatty', return_value=True), patch.dict(os.environ, TERM='xterm'), patch('curses.wrapper', side_effect=error):
        assert tui.run(lambda: observation) == expected
    observation.close.assert_called_once()


def test_control_characters_are_not_written_to_terminal():
    assert '\x1b' not in tui.safe_text('web\x1b[2J\n')


@pytest.mark.parametrize('value', ['0', '4', '3601'])
def test_refresh_bounds(value):
    with pytest.raises(SystemExit):
        cli.argv_parse_check(['tui', '--refresh', value])


def test_observation_engine_collects_without_executing_or_auditing():
    from monit_docker.core import MonitoringEngine
    from monit_docker.domain.models import ContainerSnapshot
    initial = ContainerSnapshot(id='a' * 64, name='web', status='running')
    collector = Mock()
    collector.select.return_value = [initial]
    collector.collect.return_value = initial
    executor, audit = Mock(), Mock()
    engine = MonitoringEngine(collector, executor, audit=audit)
    observation = Observation(lambda: engine.run_once(rules=(), resources=('status',)))
    observation.refresh()
    assert observation.snapshot()['containers'][0]['name'] == 'web'
    executor.execute.assert_not_called()
    assert not audit.mock_calls
    collector.end_cycle.assert_called_once()
