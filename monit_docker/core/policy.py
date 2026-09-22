"""Cooldown identities and decisions, independent of persistence and interfaces."""

import hashlib
import json
import time

from monit_docker.domain.errors import MonitoringError


class CooldownPolicy(object):
    def __init__(self, state, rules, seconds, clock=None):
        self.state = state
        self.seconds = seconds
        self.clock = clock or time.time
        # Resolve aliases and validate every identity before any action can run.
        try:
            self.identities = dict((id(rule), json.dumps(
                (rule.conditions, rule.actions), sort_keys=True, allow_nan=False,
                separators=(',', ':'))) for rule in rules)
        except (TypeError, ValueError) as error:
            raise MonitoringError(110, 'cron rules require JSON-compatible arguments: %s' % error)

    def claim(self, container_id, rule, read_only=False):
        identity = json.dumps((container_id, self.identities[id(rule)]),
                              separators=(',', ':')).encode('utf-8')
        key = hashlib.sha256(identity).hexdigest()
        return self.state.reserve(key, self.clock(), self.seconds, read_only=read_only)
