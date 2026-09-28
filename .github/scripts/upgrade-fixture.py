"""Cross-version storage probe; run only on disposable rehearsal directories."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys

from monit_docker.adapters.state import LocalState
from monit_docker.audit import AuditJournal, event_record, export_events

_KEY = 'a' * 64
_OTHER_KEY = 'b' * 64
_FUTURE = 4102444800
_CONFIG = 'conditions:\n  busy:\n    expr: ["cpu_percent > 80"]\ncommands:\n  recover:\n    exec: [restart]\n'


def snapshot(root):
    with LocalState(str(root / 'state.json')) as state:
        state_data = dict(cooldowns=state.entries, observations=state.observations,
                          restarts=state.restarts, maintenance=state.maintenance)
    journal = AuditJournal(root / 'events.jsonl', emit=False)
    records = journal.read()
    for format in ('jsonl', 'csv'):
        stream = io.StringIO()
        export_events(records, stream, format)
        assert stream.getvalue(), 'empty journal export'
    return dict(state=state_data, events=records, config=(root / 'config.yml').read_text())


def main():
    phase, directory = sys.argv[1:]
    root = Path(directory)
    if phase == 'seed':
        root.mkdir()
        (root / 'config.yml').write_text(_CONFIG)
        with LocalState(str(root / 'state.json')) as state:
            state.reserve(_KEY, _FUTURE, 60)
            state.reserve_restart(_KEY, 3)
            state.set_maintenance(_OTHER_KEY, 60, _FUTURE)
            state.replace_observations({_KEY: [_FUTURE, _FUTURE + 1]})
        # An archived v1 event and a current v2 event exercise mixed retained history.
        legacy = dict(schema_version=1, event_id='legacy-fixture', category='action',
                      event='completed', message='line\nbreak é', correlation_id='legacy-action')
        (root / 'events.jsonl.1').write_text(json.dumps(legacy) + '\n')
        AuditJournal(root / 'events.jsonl', emit=False).append(
            event_record('action', 'completed', correlation_id='seed-action'))
        (root / 'expected.json').write_text(json.dumps(snapshot(root), sort_keys=True))
    else:
        expected = json.loads((root / 'expected.json').read_text())
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in root.iterdir() if p.is_file() and not p.name.endswith('.lock')}
        assert snapshot(root) == expected, 'state, history or configuration changed across versions'
        after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in root.iterdir() if p.is_file() and not p.name.endswith('.lock')}
        assert before == after, 'read-only inspection rewrote persisted bytes'
        if phase == 'advance':
            with LocalState(str(root / 'state.json')) as state:
                assert state.reserve_restart(_KEY, 3)
                assert state.reserve_restart(_KEY, 3)
                assert not state.reserve_restart(_KEY, 3), 'restart budget was reset'
            AuditJournal(root / 'events.jsonl', emit=False).append(
                event_record('action', 'completed', correlation_id='upgrade-action'))
            current = snapshot(root)
            assert current['events'][:-1] == expected['events']
            assert len(current['events']) == len(expected['events']) + 1
            expected['state']['restarts'][_KEY] = 3
            assert current['state'] == expected['state']
            (root / 'expected.json').write_text(json.dumps(current, sort_keys=True))
    subprocess.run([sys.executable, '-m', 'monit_docker', '-c', str(root / 'config.yml'),
                    '--logfile', str(root / 'unused' / 'log'), '--runtimedir', '',
                    'check-config'], check=True, timeout=30, stdout=subprocess.PIPE)
    print(json.dumps(dict(phase=phase, result='passed')))


if __name__ == '__main__':
    main()
