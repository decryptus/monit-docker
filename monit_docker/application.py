"""Monitoring application operations independent of command and HTTP interfaces."""
import time
import uuid
import hashlib
from docker.errors import APIError, DockerException
from monit_docker.core.policy import CooldownPolicy, RestartPolicy, restart_key
from monit_docker.domain.errors import MonitoringError, ActionRejected
from monit_docker.domain.maintenance import MAINTENANCE_COMMANDS

def rule_policy(state, rules, options, audit=None, clock=None):
    now = (clock or time.time)()
    expired = [identifier for identifier, until in state.maintenance.items() if until <= now]
    fields = []
    if audit and not options.dry_run:
        for identifier in expired:
            event = dict(correlation_id=uuid.uuid4().hex, source='automatic', actor='rule-engine',
                         container_id=identifier, action='maintenance-expired')
            audit.record('action', 'started', result='pending', **event)
            fields.append(event)
    try:
        state.expire_maintenance(now, read_only=options.dry_run)
    except Exception as error:
        for event in fields:
            audit.finish('action', 'completed', result='failed', error_code=getattr(error, 'code', None), **event)
        raise
    for event in fields:
        audit.finish('action', 'completed', result='succeeded', **event)
    if not rules:
        return CooldownPolicy(state, rules, options.cooldown, clock=clock)
    return RestartPolicy(state, rules, options.cooldown, options.trigger_after,
                         options.max_gap, clock=clock, read_only=options.dry_run,
                         max_restarts=options.max_restarts)

class MonitoringApplication:
    def __init__(self, engine, rules, options, audit=None, state_factory=None, clock=None):
        self.engine, self.rules, self.options, self.audit = engine, rules, options, audit
        self.state_factory = state_factory
        self.clock = clock or (lambda: time.time())
        if options.state_file and state_factory is None:
            raise ValueError("Persistent monitoring requires a state factory")

    def cycle(self, observer):
        def run(policy=None):
            return self.engine.run_once(rules=self.rules, resources=self.options.resource,
                                        dry_run=self.options.dry_run, action_policy=policy,
                                        on_action=observer)
        try:
            if self.options.state_file:
                with self.state_factory(self.options.state_file) as state:
                    return run(rule_policy(state, self.rules, self.options, self.audit, self.clock))
            return run()
        except APIError as error:
            raise MonitoringError(180, str(error))
        except DockerException as error:
            raise MonitoringError(170, str(error))

    def manual_action(self, container_id, command):
        try:
            with self.state_factory(self.options.state_file) as state:
                def claim(identifier):
                    if command == 'restart-reset' and restart_key(identifier) not in state.restarts:
                        raise ActionRejected('no_restart_attempts')
                    key = hashlib.sha256(('manual:' + identifier).encode('ascii')).hexdigest()
                    return state.reserve(key, self.clock(), self.options.action_cooldown)
                if command in MAINTENANCE_COMMANDS and not self.options.allow_maintenance:
                    raise ActionRejected('unsupported_action')
                self.engine.run_manual_action(container_id, command, claim,
                                             reset_restarts=lambda identifier: state.reset_restarts(restart_key(identifier)),
                                             set_maintenance=lambda identifier, seconds: state.set_maintenance(identifier, seconds, self.clock()))
        except APIError as error:
            raise MonitoringError(180, str(error))
        except DockerException as error:
            raise MonitoringError(170, str(error))

    def run_once(self, on_snapshot=None, on_action=None):
        if self.options.subcommand in ('cron', 'serve'):
            return self.cycle(on_action)
        return self.engine.run_once(rules=self.rules, resources=self.options.resource,
                                    dry_run=self.options.dry_run, on_snapshot=on_snapshot,
                                    on_action=on_action)
