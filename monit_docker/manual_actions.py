"""Bounded in-memory manual requests, executed by the monitoring scheduler.

No daemon thread, Docker object, HTTP authentication or persistent audit log
belongs here. Callers serialize take/complete with monitoring cycles.
"""

from collections import OrderedDict
import copy
import re
import time
from threading import Lock

from monit_docker.core.manual import ALLOWED_STATES
from monit_docker.domain.errors import ActionRejected


_REQUEST_FIELDS    = frozenset(('request_id', 'container_id', 'action'))
_REQUEST_ID        = re.compile(r'[a-f0-9]{32}')
_CONTAINER_ID      = re.compile(r'[a-f0-9]{64}')
_HISTORY_LIMIT     = 32
_QUEUE_TIMEOUT     = 60


class ManualActions(object):
    def __init__(self, execute, origin, token, clock=None, monotonic=None):
        self.execute = execute
        self.origin = origin
        self.token = token
        self.clock = clock or time.time
        self.monotonic = monotonic or time.monotonic
        self._lock = Lock()
        self._requests = OrderedDict()
        self._pending = None
        self._active = None
        self._queued_at = None

    def status(self):
        with self._lock:
            return dict(enabled=True, allowed_states=copy.deepcopy(ALLOWED_STATES),
                        recent=copy.deepcopy(list(self._requests.values())))

    def submit(self, payload, status):
        if (not isinstance(payload, dict)
                or set(payload) != _REQUEST_FIELDS
                or not all(isinstance(value, str) for value in payload.values())
                or not _REQUEST_ID.fullmatch(payload['request_id'])
                or not _CONTAINER_ID.fullmatch(payload['container_id'])
                or payload['action'] not in ALLOWED_STATES):
            raise ActionRejected('invalid_request')
        with self._lock:
            old = self._requests.get(payload['request_id'])
            if old is not None:
                if any(old[key] != value for key, value in payload.items()):
                    raise ActionRejected('request_id_conflict')
                return copy.deepcopy(old)
            if self._pending is not None or self._active is not None:
                raise ActionRejected('busy')
            if not status['ready']:
                raise ActionRejected('not_ready')
            target = next((item for item in status['containers']
                           if item['id'] == payload['container_id']), None)
            if target is None:
                raise ActionRejected('not_selected')
            if target.get('manual_actions_protected', False):
                raise ActionRejected('container_protected')
            if target['status'] not in ALLOWED_STATES[payload['action']]:
                raise ActionRejected('state_changed')
            record = dict(payload, status='queued', submitted_at=self.clock(),
                          finished_at=None, error=None, error_code=None)
            if len(self._requests) >= _HISTORY_LIMIT:
                self._requests.popitem(last=False)
            self._requests[payload['request_id']] = record
            self._pending = payload['request_id']
            self._queued_at = self.monotonic()
            return copy.deepcopy(record)

    def take(self):
        with self._lock:
            if self._pending is None:
                return None
            identifier, self._pending = self._pending, None
            record = self._requests[identifier]
            if self.monotonic() - self._queued_at > _QUEUE_TIMEOUT:
                record.update(status='failed', error='expired', finished_at=self.clock())
                return None
            self._active = identifier
            record['status'] = 'running'
            return copy.deepcopy(record)

    def complete(self, identifier, error=None, error_code=None):
        with self._lock:
            self._requests[identifier].update(
                status='failed' if error else 'succeeded', error=error,
                error_code=error_code, finished_at=self.clock())
            self._active = None
