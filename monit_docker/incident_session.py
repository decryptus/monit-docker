# Copyright 2019-2026 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, explicit incident preview/consent service; no transport or actions."""
import copy
import secrets
import re
from threading import Lock
import time

from monit_docker.incident import IncidentAssistant, ExplanationError, json_bytes

PREVIEW_TTL = 60
MAX_PREVIEWS = 32
REQUEST_INTERVAL = 60
PREVIEW_ID_RE = re.compile(r'[0-9a-f]{64}')


def evidence_identity(context):
    # Elapsed age changes while a person reads. Freshness, capture time and every
    # observation must still match; send the ORIGINAL preview including its age.
    return json_bytes({key: value for key, value in context.items() if key != 'age_seconds'})


class IncidentSessions:
    def __init__(self, provider_loader, *, evidence_options=None, clock=time.time, timer=time.monotonic):
        self.provider_loader = provider_loader
        self.evidence_options = evidence_options or (lambda container_id, actor: {})
        self.clock, self.timer = clock, timer
        self._lock = Lock()
        self._previews = {}
        self._busy = False
        self._next_request = 0

    def preview(self, status, container_id, actor, **options):
        options = dict(self.evidence_options(container_id, actor), **options)
        report = IncidentAssistant(clock=self.clock).explain(status, container_id, preview=True, **options)
        now = self.timer()
        with self._lock:
            self._previews = {key: value for key, value in self._previews.items() if value[0] > now}
            if len(self._previews) >= MAX_PREVIEWS:
                raise ExplanationError('preview_capacity')
            key = secrets.token_hex(32)
            self._previews[key] = (now + PREVIEW_TTL, actor, copy.deepcopy(report))
        return dict(report, preview_id=key, expires_in=PREVIEW_TTL)

    def explain(self, status, container_id, preview_id, actor, **options):
        if not isinstance(preview_id, str) or not PREVIEW_ID_RE.fullmatch(preview_id):
            raise ExplanationError('invalid_preview')
        options = dict(self.evidence_options(container_id, actor), **options)
        current = IncidentAssistant(clock=self.clock).explain(status, container_id, preview=True, **options)
        with self._lock:
            stored = self._previews.get(preview_id)
            if stored is None or stored[1] != actor or stored[2]['container_id'] != container_id:
                raise ExplanationError('invalid_preview')
            if stored[0] <= self.timer():
                del self._previews[preview_id]
                raise ExplanationError('preview_expired')
            if evidence_identity(stored[2]['context']) != evidence_identity(current['context']):
                del self._previews[preview_id]
                raise ExplanationError('evidence_changed')
            if self._busy:
                raise ExplanationError('ai_busy')
            if self.timer() < self._next_request:
                raise ExplanationError('ai_rate_limited')
            self._busy = True
            self._next_request = self.timer() + REQUEST_INTERVAL
            # Consume BEFORE contacting the provider, including uncertain outcomes.
            del self._previews[preview_id]
        try:
            assistant = IncidentAssistant(self.provider_loader(), clock=self.clock, timer=self.timer)
            return assistant.analyze(stored[2])
        finally:
            with self._lock:
                self._busy = False
