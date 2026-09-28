"""Read-only observation cache, independent of terminal and command parsing."""
from copy import deepcopy
from threading import Event, Lock, Thread
import time

from monit_docker.audit_query import AuditQuery, QueryError

DEFAULT_REFRESH = 30
MIN_REFRESH = 5
MAX_REFRESH = 3600
JOIN_TIMEOUT = 0.2


class Observation:
    """One bounded-rate worker; never schedule overlapping collection cycles."""
    def __init__(self, collect, reader=None, interval=DEFAULT_REFRESH, clock=time.monotonic):
        if not MIN_REFRESH <= interval <= MAX_REFRESH:
            raise ValueError('Refresh interval must be between 5 and 3600 seconds')
        self.collect, self.reader, self.interval, self.clock = collect, reader, interval, clock
        self.stop = Event()
        self.lock = Lock()
        self.worker = None
        self.data = dict(containers=[], records=[], collection_error=None,
                         journal_error=None, updated=None, journal_more=False,
                         journal_enabled=reader is not None)

    def refresh(self):
        update = {}
        try:
            update['containers'] = [item.to_dict() for item in self.collect().snapshots]
            update['collection_error'] = None
        except Exception as error:
            update.update(containers=[], collection_error=getattr(error, 'code', type(error).__name__))
        update['updated'] = self.clock()
        if self.reader is not None:
            try:
                page = self.reader.page(AuditQuery(), 'local-tui')
                update.update(records=page['records'], journal_error=None,
                              journal_more=bool(page['next_cursor']))
            except QueryError as error:
                update.update(records=[], journal_error=error.reason, journal_more=False)
        with self.lock:
            self.data.update(update)

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.data)
        result['age'] = None if result['updated'] is None else max(0, self.clock() - result['updated'])
        result['stale'] = result['age'] is not None and result['age'] > max(90, 3 * self.interval)
        if result['stale']:
            result['containers'] = []
        return result

    def _run(self):
        while not self.stop.is_set():
            self.refresh()
            if self.stop.wait(self.interval):
                break

    def start(self):
        if self.worker is not None:
            raise RuntimeError('Observation already started')
        self.worker = Thread(target=self._run, name='monit-docker-observation', daemon=True)
        self.worker.start()

    def close(self):
        self.stop.set()
        if self.worker is not None:
            self.worker.join(JOIN_TIMEOUT)
