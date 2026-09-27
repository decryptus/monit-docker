"""Neutral monitoring job values and shared validation."""
from dataclasses import dataclass, replace
import ipaddress
import math

from monit_docker.adapters.syntax import DEFAULT_RESOURCE_CHOICES, RESOURCE_CHOICES, STATUS_RC
from monit_docker.core.policy import DEFAULT_MAX_RESTARTS
from monit_docker.domain.filesystems import filesystem_resource
from monit_docker.domain.runtime import DEFAULT_EVENT_WINDOW, valid_event_window
from monit_docker.audit import DEFAULT_MAX_BYTES

JOB_MODES = frozenset(('stats', 'cron', 'serve', 'monit'))
POLICY_MODES = frozenset(('cron', 'serve'))
OUTPUT_MODES = frozenset(('text', 'json'))


@dataclass(frozen=True)
class JobOptions:
    subcommand: str = 'stats'
    conffile: str = '/etc/monit-docker/monit-docker.yml'
    client: str = None
    client_from_env: bool = False
    id: tuple = ()
    name: tuple = ()
    image: tuple = ()
    label: tuple = ()
    ctn_grp: tuple = ()
    status: tuple = ()
    cmd: tuple = ()
    resource: tuple = ()
    output: str = 'json'
    state_file: str = None
    dry_run: bool = False
    cooldown: float = 300
    max_restarts: int = DEFAULT_MAX_RESTARTS
    trigger_after: float = 0
    max_gap: float = None
    interval: float = 30
    stale_after: float = None
    bind: str = '127.0.0.1'
    port: int = 9808
    event_window: int = DEFAULT_EVENT_WINDOW
    audit_file: str = None
    audit_max_bytes: int = DEFAULT_MAX_BYTES
    audit_files: int = 5
    allow_actions: bool = False
    allow_maintenance: bool = False
    action_cooldown: float = 30
    notifications_enabled: bool = False
    audit_read_enabled: bool = False


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value, minimum):
    return type(value) in (int, float) and math.isfinite(value) and value >= minimum


def validate_job(job):
    require(job.subcommand in JOB_MODES, 'Unknown monitoring mode')
    require(valid_event_window(job.event_window), '--event-window must be between 1 and 86400 seconds')
    require(job.audit_file is None or isinstance(job.audit_file, str) and bool(job.audit_file.strip()), '--audit-file must not be empty')
    require(type(job.audit_max_bytes) is int and job.audit_max_bytes >= 65536
            and type(job.audit_files) is int and 1 <= job.audit_files <= 100,
            'Audit retention requires at least 65536 bytes and 1..100 files')
    require(job.output in OUTPUT_MODES, 'Unknown output format')
    require(all(value in STATUS_RC for value in job.status), 'Unknown container status')
    require(all(value in RESOURCE_CHOICES or filesystem_resource(value) for value in job.resource), 'Unknown resource')
    if job.subcommand in POLICY_MODES:
        require(finite(job.cooldown, 0), '--cooldown must be finite and non-negative')
        require(type(job.max_restarts) is int and job.max_restarts >= 1, '--max-restarts must be a positive integer')
        require(finite(job.trigger_after, 0), '--trigger-after must be finite and non-negative')
        if job.trigger_after:
            require(bool(job.cmd), '--trigger-after requires --cmd or --cmd-if')
            require(finite(job.max_gap, 0) and job.max_gap > 0, '--trigger-after requires a finite positive --max-gap')
        else:
            require(job.max_gap is None, '--max-gap requires a positive --trigger-after')
        require(job.state_file is None or isinstance(job.state_file, str) and bool(job.state_file.strip()), '--state-file must not be empty')
        require(not job.dry_run or bool(job.cmd), '--dry-run requires --cmd or --cmd-if')
    if job.subcommand == 'cron':
        require(bool(job.cmd), 'cron requires --cmd or --cmd-if')
        require(bool(job.state_file), '--state-file is required')
    if job.subcommand == 'serve':
        try:
            ipaddress.IPv4Address(job.bind)
        except (ValueError, TypeError):
            raise ValueError('--bind must be an IPv4 address') from None
        require(type(job.port) is int and 1 <= job.port <= 65535, '--port must be between 1 and 65535')
        require(finite(job.interval, 0.1), '--interval must be finite and at least 0.1 seconds')
        job = replace(job, stale_after=max(90, 3 * job.interval) if job.stale_after is None else job.stale_after)
        require(finite(job.stale_after, job.interval), '--stale-after must be finite and at least --interval')
        require(not job.cmd or bool(job.state_file), 'serve with rules requires --state-file')
        require(not job.trigger_after or job.max_gap > job.interval, '--max-gap must exceed --interval to allow time for collection')
        require(not job.allow_maintenance or job.allow_actions, '--allow-maintenance requires --allow-actions')
        require(not job.allow_actions or bool(job.state_file) and not job.dry_run, 'Manual actions require state and cannot run in dry-run mode')
        require(finite(job.action_cooldown, 1), '--action-cooldown must be finite and at least 1 second')
    if job.subcommand in ('stats', 'serve') and not job.resource:
        job = replace(job, resource=DEFAULT_RESOURCE_CHOICES)
    return job
