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
from monit_docker.core.manual import ALLOWED_STATES
from monit_docker.domain.errors import ActionRejected, MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import Action, ActionDecision, ActionResult, CycleResult

LOG = logging.getLogger('monit-docker')


class MonitoringEngine(object):
    def __init__(self, collector, executor, evaluator=None):
        self.collector = collector
        self.executor = executor
        self.evaluator = evaluator or RuleEvaluator()
        self._cycle_lock = Lock()

    def run_manual_action(self, container_id, command, claim):
        """Reselect by exact ID and serialize with cycles; never resolve aliases.

        claim(id) reserves a persistent per-container manual cooldown before
        execution. Manual operations do not evaluate autonomous rules.
        """
        if command not in ALLOWED_STATES:
            raise ActionRejected('unsupported_action')
        if not self._cycle_lock.acquire(False):
            raise ActionRejected('busy')
        try:
            try:
                self.collector.begin_cycle()
                selected = {item.id: item for item in self.collector.select()}
                if container_id not in selected:
                    raise ActionRejected('not_selected')
                if selected[container_id].manual_actions_protected:
                    raise ActionRejected('container_protected')
                if selected[container_id].status not in ALLOWED_STATES[command]:
                    raise ActionRejected('state_changed')
                if not claim(container_id):
                    raise ActionRejected('cooldown')
                action = Action('docker', command, (), {})
                if not self.executor.execute(container_id, action):
                    raise MonitoringError(116, 'manual action failed')
            finally:
                failed = sys.exc_info()[0] is not None
                try:
                    self.collector.end_cycle()
                except Exception:
                    if not failed:
                        raise
                    LOG.exception('collector cleanup failed; preserving the action error')
        finally:
            self._cycle_lock.release()

    def run_once(self, rules=(), resources=None, on_snapshot=None,
                 dry_run=False, action_policy=None, on_action=None):
        """Run a fresh cycle and return raw snapshots and successful actions.

        Rules with only PID/status conditions run before sampling, preserving
        the CLI's existing two-phase ordering. The first failed action aborts
        the cycle. With no rules, resources defaults to all measurements;
        with rules, it defaults to only the measurements those rules need.
        Explicit resources are collected in addition to rule requirements.
        on_snapshot, if supplied, consumes each completed container in order;
        its exceptions abort the cycle after releasing collector resources.
        dry_run reports matching actions without calling the executor. An
        optional action_policy claims a rule before its first action. Its optional
        observe(id, rule, matched, read_only=False) hook can delay a matched rule;
        on_action receives skipped, simulated and successful action decisions.
        No stdout, process exit, scheduler or persistent state belongs here.
        """
        if not self._cycle_lock.acquire(False):
            raise RuntimeError('a monitoring cycle is already running')
        try:
            rules = tuple(rules)
            if resources is None:
                resources = () if rules else ContainerSnapshot.RESOURCE_FIELDS
            resources = tuple(resources)
            invalid = set(resources) - set(ContainerSnapshot.RESOURCE_FIELDS)
            if invalid:
                raise ValueError('unknown resources: %s' % ', '.join(sorted(invalid)))
            return self._run_cycle(rules, resources, on_snapshot, dry_run,
                                   action_policy, on_action)
        finally:
            self._cycle_lock.release()

    def _run_cycle(self, rules, resources, on_snapshot, dry_run, action_policy, on_action):
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
                            self._apply(rule, snapshot, actions, dry_run, action_policy, on_action)
                    snapshot = self.collector.describe(initial.id)
                    needed = tuple(dict.fromkeys(
                        tuple(r for rule in pending for r in rule.resources) + resources))
                    if needed and snapshot.status in ('running', 'paused'):
                        snapshot = self.collector.collect(snapshot, needed)
                        for rule in pending:
                            snapshot = self.collector.describe(initial.id, snapshot)
                            self._apply(rule, snapshot, actions, dry_run, action_policy, on_action)
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

    def _apply(self, rule, snapshot, results, dry_run, action_policy, on_action):
        matched = self.evaluator.matches(rule, snapshot)
        observe = getattr(action_policy, 'observe', None)
        ready = observe is None or observe(snapshot.id, rule, matched, read_only=dry_run)
        if not matched:
            return
        allowed = ready and (action_policy is None or action_policy.claim(
            snapshot.id, rule, read_only=dry_run))
        status = ('pending' if not ready else 'cooldown' if not allowed
                  else 'dry-run' if dry_run else 'execute')
        for action in rule.actions:
            if status != 'execute':
                if on_action:
                    on_action(ActionDecision(snapshot.id, rule.source, action.command, status))
                continue
            success = self.executor.execute(snapshot.id, action)
            if not success:
                raise MonitoringError(116, 'command failed on %s: %r' %
                                      (snapshot.name, action.command))
            results.append(ActionResult(snapshot.id, rule.source, action.command, True))
            if on_action:
                on_action(ActionDecision(snapshot.id, rule.source, action.command, 'executed'))
