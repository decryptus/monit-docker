"""Sequential monitoring loop and synchronized cache; no HTTP or Docker imports."""

import copy
import logging
from threading import Lock
import time

LOG = logging.getLogger('monit-docker')


class MonitorService(object):
    def __init__(self, cycle, interval=30, stale_after=90, clock=None, monotonic=None):
        self.cycle = cycle
        self.interval = interval
        self.stale_after = stale_after
        self.clock = clock or time.time
        self.monotonic = monotonic or time.monotonic
        self._lock = Lock()
        self._last_success_tick = None
        self._data = dict(api_version=1, running=False, last_cycle_success=False,
                          last_cycle_finished_at=None, last_success_at=None,
                          last_error_code=None, cycles_total=0, errors_total=0,
                          actions=dict(executed=0, cooldown=0, pending=0, **{'dry-run': 0}),
                          containers=[])

    def status(self):
        with self._lock:
            data = copy.deepcopy(self._data)
            age = (None if self._last_success_tick is None else
                   max(0, self.monotonic() - self._last_success_tick))
        data['age_seconds'] = age
        data['ready'] = bool(data['last_cycle_success'] and age is not None
                             and age <= self.stale_after)
        # Do not expose old measurements as current after failure or staleness.
        if not data['ready']:
            data['containers'] = []
        return data

    def _observe(self, decision):
        with self._lock:
            self._data['actions'][decision.status] += 1

    def run_cycle(self):
        with self._lock:
            if self._data['running']:
                raise RuntimeError('service cycle already running')
            self._data['running'] = True
        update = {}
        success_tick = None
        try:
            result = self.cycle(self._observe)
            containers = [snapshot.to_dict() for snapshot in result.snapshots]
        except Exception as error:
            LOG.exception('monitoring cycle failed')
            update = dict(last_cycle_success=False, containers=[],
                          last_error_code=getattr(error, 'code', 150))
        else:
            success_tick = self.monotonic()
            update = dict(last_cycle_success=True, last_success_at=self.clock(),
                          last_error_code=None, containers=containers)
        finally:
            with self._lock:
                if success_tick is not None:
                    self._last_success_tick = success_tick
                self._data.update(update, running=False, last_cycle_finished_at=self.clock())
                if update.get('last_cycle_success') is False:
                    self._data['errors_total'] += 1
                self._data['cycles_total'] += 1

    def run(self, stop):
        while not stop.is_set():
            self.run_cycle()
            # Delay after completion: slow cycles never overlap or catch up.
            if stop.wait(self.interval):
                break
