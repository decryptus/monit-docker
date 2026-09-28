"""Offline state operations shared by operator interfaces."""
import time
import uuid

from monit_docker.core.policy import restart_key
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.maintenance import MAX_MAINTENANCE_SECONDS
from monit_docker.domain.identifiers import CONTAINER_ID_RE


class StateOperations:
    def __init__(self, state_factory, audit, clock=None):
        self.state_factory = state_factory
        self.audit = audit
        self.clock = clock or time.time

    @staticmethod
    def validate(path, identifier):
        if not isinstance(path, str) or not path.strip():
            raise MonitoringError(110, 'state path must not be empty')
        if not isinstance(identifier, str) or not CONTAINER_ID_RE.fullmatch(identifier):
            raise MonitoringError(110, 'container ID must be 64 lowercase hexadecimal characters')

    def reset_restarts(self, path, identifier, actor):
        self.validate(path, identifier)
        with self.state_factory(path) as state:
            key = restart_key(identifier)
            if key not in state.restarts:
                raise MonitoringError(110, 'no restart attempts recorded for this container')
            fields = dict(correlation_id=uuid.uuid4().hex, source='manual', actor=actor,
                          container_id=identifier, action='restart-reset')
            self.audit.record('action', 'started', result='pending', **fields)
            try:
                state.reset_restarts(key)
            except Exception as error:
                self.audit.finish('action', 'completed', result='failed', reason=type(error).__name__,
                                  error_code=getattr(error, 'code', None), **fields)
                raise
            self.audit.finish('action', 'completed', result='succeeded', **fields)
        return dict(container_id=identifier, status='rearmed')

    def maintenance(self, path, identifier, duration, actor):
        self.validate(path, identifier)
        if type(duration) is not int or not 0 <= duration <= MAX_MAINTENANCE_SECONDS:
            raise MonitoringError(110, 'duration must be between 0 and 86400 seconds')
        with self.state_factory(path) as state:
            now = self.clock()
            fields = dict(correlation_id=uuid.uuid4().hex, source='manual', actor=actor,
                          container_id=identifier, action='maintenance',
                          reason='duration_seconds=%d' % duration)
            self.audit.record('action', 'started', result='pending', **fields)
            try:
                state.set_maintenance(identifier, duration, now)
            except Exception as error:
                self.audit.finish('action', 'completed', result='failed',
                                  error_code=getattr(error, 'code', None), **fields)
                raise
            self.audit.finish('action', 'completed', result='succeeded', **fields)
        return dict(container_id=identifier, maintenance_until=now + duration if duration else None)
