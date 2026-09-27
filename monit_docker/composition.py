"""Neutral application assembly; no command parser or terminal dependencies."""
import os
from monit_docker.adapters.validation import CheckedConfiguration, validate_configuration, ConfigurationCheckError
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor, client_factory
from monit_docker.domain.filesystems import filesystem_resource
from monit_docker.domain.errors import MonitoringError
from monit_docker.core import MonitoringEngine
from monit_docker.application import MonitoringApplication
from monit_docker.audit import DEFAULT_MAX_BYTES
from monit_docker.job import validate_job
from monit_docker.adapters.state import LocalState

def audit_journal(options, explicit=False):
    from monit_docker.audit import AuditJournal
    path = getattr(options, 'audit_file', None)
    if not path:
        if explicit:
            raise MonitoringError(110, 'Specify --audit-file for export or forwarding')
        state = getattr(options, 'state_file', None)
        directory = (os.path.join(os.path.dirname(os.path.abspath(state)), 'audit') if state else
                     os.path.join(os.environ.get('XDG_STATE_HOME') or os.path.expanduser('~/.local/state'), 'monit-docker', 'audit'))
        path = os.path.join(directory, 'events.jsonl')
    return AuditJournal(path, getattr(options, 'audit_max_bytes', DEFAULT_MAX_BYTES),
                        getattr(options, 'audit_files', 5))

def build_application(options, config=None, inline=None, use_rules=True, actor='local-operator'):
    options = validate_job(options)
    try:
        if config is None:
            config = CheckedConfiguration(options.conffile, inline).load(allow_missing=True)
        validate_configuration(config, client=options.client, from_env=options.client_from_env)
    except ConfigurationCheckError as error:
        raise MonitoringError(110, '%s: %s' % (error.location, error)) from None
    selector = ContainerSelector(
        selectors=dict((kind, getattr(options, kind)) for kind in ('id', 'name', 'label', 'image')),
        statuses=options.status, groups=config.get('ctn-groups'), selected_groups=options.ctn_grp)
    rules = ()
    if use_rules:
        parser = RuleParser(config.get('commands'), config.get('conditions'), config.get('dir-groups'))
        rules = tuple(parser.parse(expression) for expression in options.cmd)
    collector = DockerCollector(client_factory(config, options.client, options.client_from_env),
                                selector, config.get('dir-groups'), options.event_window)
    for resource in options.resource or ():
        filesystem = filesystem_resource(resource)
        if filesystem and filesystem[1] not in collector.dir_groups:
            raise MonitoringError(110, 'unknown directory group: %s' % filesystem[1])
    audit = None
    if rules or getattr(options, 'allow_actions', False) or options.notifications_enabled or options.audit_read_enabled:
        audit = audit_journal(options)
    source = 'manual' if options.subcommand == 'monit' else 'automatic'
    actor = actor if source == 'manual' else 'cron' if options.subcommand == 'cron' else 'rule-engine'
    engine = MonitoringEngine(collector, DockerActionExecutor(collector), audit=audit,
                                   audit_source=source, audit_actor=actor)
    return MonitoringApplication(engine, rules, options, audit, state_factory=LocalState)
