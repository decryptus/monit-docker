"""Cooldown identities and decisions, independent of persistence and interfaces."""

import hashlib
import json
import math
import time

from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot

DEFAULT_MAX_RESTARTS = 3


def restart_key(container_id):
    return hashlib.sha256(('restart:' + container_id).encode('utf-8')).hexdigest()


def is_restart(action):
    return action.kind == 'docker' and action.command == 'restart'


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

    def key(self, container_id, rule):
        identity = json.dumps((container_id, self.identities[id(rule)]),
                              separators=(',', ':')).encode('utf-8')
        return hashlib.sha256(identity).hexdigest()

    def observe(self, container_id, rule, matched, read_only=False):
        return True

    def claim(self, container_id, rule, read_only=False):
        return self.state.reserve(self.key(container_id, rule), self.clock(), self.seconds,
                                  read_only=read_only)


class TriggerPolicy(CooldownPolicy):
    """One cycle's observed-condition durations, sharing the cooldown store.

    Invalidate durable observations before collecting anything. Only conditions
    actually observed true in this cycle are written back. Thus a crash, failed
    collection, stopped or unselected container cannot carry an unobserved streak
    into the next cycle. Earlier successful observations in a partial cycle remain
    valid; this is intentionally per rule/container, not a cycle transaction.
    """
    def __init__(self, state, rules, seconds, trigger_after=0, max_gap=None,
                 clock=None, read_only=False):
        super(TriggerPolicy, self).__init__(state, rules, seconds, clock)
        if (not math.isfinite(trigger_after) or trigger_after < 0
                or (trigger_after > 0 and (max_gap is None or not math.isfinite(max_gap)
                                          or max_gap <= 0))):
            raise ValueError('invalid trigger duration or observation gap')
        self.trigger_after = trigger_after
        self.max_gap = max_gap
        self.previous = dict(state.observations)
        state.replace_observations({}, read_only=read_only)

    def observe(self, container_id, rule, matched, read_only=False):
        if not self.trigger_after or not rule.conditions:
            return True
        identity = json.dumps((self.key(container_id, rule), self.trigger_after, self.max_gap),
                              separators=(',', ':')).encode('utf-8')
        key = hashlib.sha256(identity).hexdigest()
        observations = dict(self.state.observations)
        if not matched:
            self.previous.pop(key, None)
            observations.pop(key, None)
            self.state.replace_observations(observations, read_only=read_only)
            return False
        now = self.clock()
        if not math.isfinite(now) or now < 0:
            raise ValueError('invalid observation time')
        since, last = self.previous.get(key, (now, now))
        if now < last or now - last > self.max_gap:
            since = now
        observations[key] = [since, now]
        self.state.replace_observations(observations, read_only=read_only)
        self.previous[key] = observations[key]
        return now - since >= self.trigger_after


class RestartPolicy(TriggerPolicy):
    """A per-container automatic restart budget, latched until explicit rearm.

    Aliases and rule identities share a budget. Health changes and process
    restarts never reset it. The state lock covers preflight and reservations.
    """
    def __init__(self, state, rules, seconds, trigger_after=0, max_gap=None,
                 clock=None, read_only=False, max_restarts=DEFAULT_MAX_RESTARTS):
        if type(max_restarts) is not int or max_restarts < 1:
            raise ValueError('restart limit must be a positive integer')
        super().__init__(state, rules, seconds, trigger_after, max_gap, clock, read_only)
        self.max_restarts = max_restarts

    def permits(self, container_id, rule):
        count = sum(is_restart(action) for action in rule.actions)
        return not count or self.state.restarts.get(restart_key(container_id), 0) + count <= self.max_restarts

    def claim_action(self, container_id, action, read_only=False):
        return not is_restart(action) or self.state.reserve_restart(
            restart_key(container_id), self.max_restarts, read_only=read_only)

    def decorate(self, snapshot):
        values = snapshot.to_dict()
        values.update(restart_attempts=self.state.restarts.get(restart_key(snapshot.id), 0),
                      restart_limit=self.max_restarts)
        return ContainerSnapshot(**values)
