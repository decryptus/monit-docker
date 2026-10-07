"""Optional Textual presentation over the existing read-only Observation service."""
import hashlib
import json
import os
import sys

from dwho.tui.textual import DashboardApp, TableRow

POLL_SECONDS = 0.25
COLUMNS = ('RESOURCE / EVENT', 'STATE', 'CPU / ACTION', 'MEMORY / RESULT')
NAVIGATION = (('containers', 'Containers'), ('journal', 'Journal'))


def row_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def percent(value):
    return 'unavailable' if value is None else '%s %%' % value


class MonitorApp(DashboardApp):
    def __init__(self, observation, demo=False):
        super().__init__(product='monit-docker', heading='Container monitoring',
                         columns=COLUMNS, navigation=NAVIGATION, mode='read_only',
                         subtitle='SYNTHETIC DEMO' if demo else 'LOCAL OBSERVATION')
        self.observation, self.demo = observation, demo
        self.view = 'containers'
        self._displayed = None

    def on_mount(self):
        self.refresh_snapshot()
        self.set_interval(POLL_SECONDS, self.refresh_snapshot)

    def on_dashboard_app_navigation_requested(self, message):
        self.view = message.key
        self.select_navigation(self.view)
        self.action_clear_search()
        self._displayed = None
        self.refresh_snapshot()

    def refresh_snapshot(self):
        if not self.is_running:
            return
        data = self.observation.snapshot()
        journal = self.view == 'journal'
        error = data['journal_error'] if journal else data['collection_error']
        state = 'info'
        if error is not None:
            state, status = 'failure', 'Unavailable: %s' % error
        elif journal and not data['journal_enabled']:
            status = 'Journal disabled: specify --audit-file'
        elif data['updated'] is None:
            state, status = 'running', 'Collecting observations...'
        elif data['stale']:
            state, status = 'warning', 'STALE: collection has not completed recently'
        else:
            status = 'Updated %.0fs ago | refresh %ss' % (data['age'], self.observation.interval)
        if journal and data['journal_more']:
            status += ' | Latest page only; more history exists'
        if self.demo:
            status = 'DEMO — synthetic data | ' + status
        self.set_notice(state, status)
        source = data['records'] if journal else data['containers']
        # Do not reset scroll or selection on every UI timer tick.
        if source == self._displayed:
            return
        rows = []
        occurrences = {}
        for item in source:
            if journal:
                identity = row_key(item)
                occurrences[identity] = occurrences.get(identity, 0) + 1
                key = '%s:%s' % (identity, occurrences[identity])
                cells = (item.get('container_name') or item.get('timestamp', ''),
                         item.get('category', ''), item.get('action') or '', item.get('result', ''))
                title = 'Journal event | %s' % item.get('timestamp', '')
            else:
                key = row_key(item.get('id') or item.get('name'))
                cells = (item.get('name', ''), '%s / %s' % (item.get('status'), item.get('health')),
                         percent(item.get('cpu_percent')), percent(item.get('mem_percent')))
                title = item.get('name', 'Container')
            rows.append(TableRow(key, cells, title, json.dumps(item, indent=2, ensure_ascii=False)))
        self.display_rows(rows)
        self._displayed = source
        self.set_activity('%s entries | READ ONLY\nNo rule evaluation or intervention is started by this interface.' % len(rows))


def run(create_observation):
    from dwho.cli import require_terminal
    from monit_docker.tui import terminal_signals, _TerminalExit
    try:
        require_terminal('tui requires an interactive terminal on stdin and stdout')
        if os.environ.get('TERM', '') in ('', 'dumb'):
            raise ValueError('TERM is unavailable')
    except ValueError:
        print('tui requires an interactive terminal on stdin and stdout', file=sys.stderr)
        return 2
    observation = create_observation()
    try:
        observation.start()
        with terminal_signals():
            MonitorApp(observation).run()
        return 0
    except _TerminalExit as error:
        return 128 + error.signum
    except KeyboardInterrupt:
        return 130
    except OSError:
        print('tui terminal unavailable or disconnected', file=sys.stderr)
        return 2
    finally:
        observation.close()
