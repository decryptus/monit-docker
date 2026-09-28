"""Failure injection and process-boundary recovery on disposable state only."""
from concurrent.futures import ThreadPoolExecutor
import errno
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.state import LocalState
from monit_docker.audit import AuditError, AuditJournal, event_record
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.manual_actions import ManualActions
from monit_docker.service import MonitorService

_KEY = 'a' * 64
_CHILD = '''
import sys, time
from monit_docker.adapters.state import LocalState
with LocalState(sys.argv[1]) as state:
    state.reserve_restart('a'*64, 1)
    print('reserved', flush=True)
    time.sleep(60)
'''


class ResilienceTests(unittest.TestCase):
    def test_killed_process_releases_lock_without_resetting_restart_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'state.json')
            child = subprocess.Popen([sys.executable, '-c', _CHILD, path], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
            try:
                with selectors.DefaultSelector() as ready:
                    ready.register(child.stdout, selectors.EVENT_READ)
                    self.assertTrue(ready.select(5), 'child failed to reserve')
                    self.assertEqual(child.stdout.readline().strip(), 'reserved')
                with self.assertRaises(MonitoringError) as failure:
                    with LocalState(path):
                        pass
                self.assertEqual(failure.exception.code, 117)
                child.kill(); child.wait(timeout=5)
                with LocalState(path) as recovered:
                    self.assertEqual(recovered.restarts[_KEY], 1)
                    self.assertFalse(recovered.reserve_restart(_KEY, 1))
            finally:
                if child.poll() is None:
                    child.kill(); child.wait(timeout=5)
                child.stdout.close(); child.stderr.close()

    def test_state_disk_full_before_replace_preserves_last_durable_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with LocalState(str(path)) as state:
                state.reserve_restart(_KEY, 3)
                before = path.read_bytes()
                with patch('monit_docker.adapters.state.os.fsync', side_effect=OSError(errno.ENOSPC, 'full')):
                    with self.assertRaises(MonitoringError) as failure:
                        state.reserve_restart(_KEY, 3)
                self.assertEqual(failure.exception.code, 118)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(state.restarts[_KEY], 1)
            with LocalState(str(path)) as recovered:
                self.assertTrue(recovered.reserve_restart(_KEY, 3))
                self.assertEqual(recovered.restarts[_KEY], 2)

    def test_partial_journal_write_fails_explicitly_and_does_not_hide_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.jsonl'
            journal = AuditJournal(path, emit=False)
            journal.append(event_record('action', 'completed'))
            before = path.read_bytes()
            write = os.write
            calls = []
            def fail_after_prefix(fd, data):
                if not calls:
                    calls.append(True)
                    return write(fd, data[:10])
                raise OSError(errno.ENOSPC, 'full')
            with patch('monit_docker.audit.os.write', side_effect=fail_after_prefix):
                with self.assertRaises(AuditError):
                    journal.append(event_record('action', 'completed'))
            self.assertEqual(path.read_bytes()[:len(before)], before)
            reopened = AuditJournal(path, emit=False)
            with self.assertRaises(AuditError):
                reopened.read()
            with self.assertRaises(AuditError):
                reopened.append(event_record('action', 'completed'))
            # Explicit operator recovery from a verified fixture backup; no auto repair.
            path.write_bytes(before)
            reopened.append(event_record('action', 'completed'))
            self.assertEqual(len(reopened.read()), 2)

    def test_simultaneous_duplicate_actions_execute_once(self):
        execute = Mock()
        actions = ManualActions(execute)
        result = CycleResult((ContainerSnapshot(id=_KEY, name='web', status='running'),), ())
        monitor = MonitorService(lambda observer: result, manual_actions=actions)
        monitor.run_cycle()
        request = dict(request_id='b'*32, container_id=_KEY, action='restart')
        with ThreadPoolExecutor(max_workers=16) as pool:
            responses = list(pool.map(lambda _: actions.submit(request.copy(), monitor.status()), range(64)))
        self.assertTrue(all(r['status'] == 'queued' for r in responses))
        self.assertTrue(monitor.run_pending_action())
        self.assertFalse(monitor.run_pending_action())
        execute.assert_called_once_with(_KEY, 'restart')
