"""One-shot orchestration shared by CLI and future local interfaces.

Collectors expose begin_cycle(), select(), describe(id, snapshot=None),
collect(snapshot, resources), and end_cycle(). Executors expose
execute(container_id, action) -> bool. Only snapshots and IDs cross this
boundary, never Docker SDK objects. One engine supports sequential cycles;
overlapping calls are rejected, not queued.
"""

import logging
import sys
from threading import Lock

from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import ActionResult, CycleResult

LOG = logging.getLogger('monit-docker')


class MonitoringEngine(object):
    def __init__(self, collector, executor, evaluator=None):
        self.collector = collector
        self.executor = executor
        self.evaluator = evaluator or RuleEvaluator()
        self._cycle_lock = Lock()

    def run_once(self, rules=(), resources=None, on_snapshot=None):
        """Run a fresh cycle and return raw snapshots and successful actions.

        Rules with only PID/status conditions run before sampling, preserving
        the CLI's existing two-phase ordering. The first failed action aborts
        the cycle. With no rules, resources defaults to all measurements;
        with rules, it defaults to only the measurements those rules need.
        Explicit resources are collected in addition to rule requirements.
        on_snapshot, if supplied, consumes each completed container in order;
        its exceptions abort the cycle after releasing collector resources.
        No stdout, process exit, scheduler or persistent state belongs here.
        """
        if not self._cycle_lock.acquire(False):
            raise RuntimeError('a monitoring cycle is already running')
        try:
            rules = tuple(rules)
            if resources is None:
                resources = () if rules else ContainerSnapshot.FIELDS[2:]
            resources = tuple(resources)
            invalid = set(resources) - set(ContainerSnapshot.FIELDS[2:])
            if invalid:
                raise ValueError('unknown resources: %s' % ', '.join(sorted(invalid)))
            return self._run_cycle(rules, resources, on_snapshot)
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, rules, resources, on_snapshot):
        snapshots, actions = [], []
        try:
            self.collector.begin_cycle()
            containers = self.collector.select()
            if not containers:
                raise MonitoringError(114, 'no container found')
            for initial in containers:
                snapshot = initial
                if rules:
                    pending = []
                    for rule in rules:
                        if rule.needs_metrics:
                            pending.append(rule)
                        else:
                            snapshot = self.collector.describe(initial.id)
                            self._apply(rule, snapshot, actions)
                    snapshot = self.collector.describe(initial.id)
                    needed = tuple(dict.fromkeys(
                        tuple(r for rule in pending for r in rule.resources) + resources))
                    if needed and snapshot.status in ('running', 'paused'):
                        snapshot = self.collector.collect(snapshot, needed)
                        for rule in pending:
                            snapshot = self.collector.describe(initial.id, snapshot)
                            self._apply(rule, snapshot, actions)
                else:
                    snapshot = self.collector.collect(snapshot, resources)
                snapshots.append(snapshot)
                if on_snapshot:
                    on_snapshot(snapshot)
            return CycleResult(tuple(snapshots), tuple(actions))
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                self.collector.end_cycle()
            except Exception:
                if not failed:
                    raise
                LOG.exception('collector cleanup failed; preserving the cycle error')

    def _apply(self, rule, snapshot, results):
        if not self.evaluator.matches(rule, snapshot):
            return
        for action in rule.actions:
            success = self.executor.execute(snapshot.id, action)
            if not success:
                raise MonitoringError(116, 'command failed on %s: %r' %
                                      (snapshot.name, action.command))
            results.append(ActionResult(snapshot.id, rule.source, action.command, True))
