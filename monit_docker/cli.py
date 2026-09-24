#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2019-2022 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy CLI: argument parsing, composition, output and process exit codes."""

from __future__ import absolute_import

import argparse
import getpass
import json
import logging
import math
import os
import sys
from logging.handlers import WatchedFileHandler

import six
import docker
from docker.errors import APIError, DockerException
from sonicprobe import helpers

from monit_docker.audit import DEFAULT_MAX_BYTES
from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor, client_factory
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.syntax import RESOURCE_CHOICES, STATUS_RC, HEALTH_RC
from monit_docker.core import MonitoringEngine
from monit_docker.core.policy import CooldownPolicy, RestartPolicy, DEFAULT_MAX_RESTARTS, restart_key
from monit_docker.domain.errors import ActionRejected, CommandExecutionError, MonitoringError
from monit_docker.outputs.formatting import format_resource
from monit_docker.domain.filesystems import filesystem_resource

SYSLOG_NAME = 'monit-docker'
LOG = logging.getLogger(SYSLOG_NAME)
DEFAULT_CONFFILE = '/etc/monit-docker/monit-docker.yml'
DEFAULT_LOGFILE = '/var/log/monit-docker/monit-docker.log'
DEFAULT_RUNTIMEDIR = '/run/monit-docker'
MONIT_DOCKER_CONFIG = os.environ.get('MONIT_DOCKER_CONFIG')
MONIT_DOCKER_CONFFILE = os.environ.get('MONIT_DOCKER_CONFFILE') or DEFAULT_CONFFILE
MONIT_DOCKER_LOGFILE = os.environ.get('MONIT_DOCKER_LOGFILE') or DEFAULT_LOGFILE
MONIT_DOCKER_RUNTIMEDIR = os.environ.get('MONIT_DOCKER_RUNTIMEDIR') or DEFAULT_RUNTIMEDIR
_SUBCMDS = {}


def _resource_argument(value):
    if value not in RESOURCE_CHOICES and not filesystem_resource(value):
        raise argparse.ArgumentTypeError('unknown resource; filesystem resources use disk_percent[group] or inode_percent[group]')
    return value

def argv_parse_check():
    """
    Parse (and check a little) command line parameters
    """
    parser        = argparse.ArgumentParser()

    parser.add_argument("-c",
                        dest    = 'conffile',
                        default = MONIT_DOCKER_CONFFILE,
                        help    = "Use configuration file <conffile> instead of %(default)s")
    parser.add_argument("--client",
                        dest    = 'client',
                        default = None,
                        help    = "choose client configuration")
    parser.add_argument("--client-from-env",
                        action  = 'store_true',
                        dest    = 'client_from_env',
                        default = False,
                        help    = "load client configuration from environment variables")
    parser.add_argument("--ctn-group",
                        action  = 'append',
                        dest    = 'ctn_grp',
                        default = [],
                        help    = "select container group from configuration file")
    parser.add_argument("--id",
                        action  = 'append',
                        dest    = 'id',
                        default = [],
                        help    = "match containers by id")
    parser.add_argument("--image",
                        action  = 'append',
                        dest    = 'image',
                        default = [],
                        help    = "match containers by image")
    parser.add_argument("--label",
                        action  = 'append',
                        dest    = 'label',
                        default = [],
                        help    = "match containers by label")
    parser.add_argument("-s",
                        action  = 'append',
                        dest    = 'status',
                        default = [],
                        choices = list(STATUS_RC),
                        help    = "match containers by status")
    parser.add_argument("-l",
                        dest    = 'loglevel',
                        default = 'info',   # warning: see affectation under
                        choices = ('critical', 'error', 'warning', 'info', 'debug'),
                        help    = ("emit traces with LOGLEVEL details, must be one"))
    parser.add_argument("--logfile",
                        dest      = 'logfile',
                        default   = MONIT_DOCKER_LOGFILE,
                        help      = "Use log file <logfile> instead of %(default)s")
    parser.add_argument("--runtimedir",
                        dest      = 'runtimedir',
                        default   = MONIT_DOCKER_RUNTIMEDIR,
                        help      = "Use runtime directory <runtimedir> instead of %(default)s")
    parser.add_argument("--name",
                        action  = 'append',
                        dest    = 'name',
                        default = [],
                        help    = "match containers by name")

    parser.add_argument('--audit-file', default=os.environ.get('MONIT_DOCKER_AUDIT_FILE'),
                        help='persistent event journal (default: audit/events.jsonl beside state file, otherwise user state directory)')
    parser.add_argument('--audit-max-bytes', type=int, default=DEFAULT_MAX_BYTES, help='maximum bytes per audit file (default: 5 MiB)')
    parser.add_argument('--audit-files', type=int, default=5, help='total retained audit files including active file (default: 5)')
    subparsers    = parser.add_subparsers(dest = 'subcommand',
                                          help = "choice sub-command")

    for subcmd in six.itervalues(_SUBCMDS):
        subcmd.load_subcmd_parser(subparsers)

    options, args = parser.parse_known_args()

    if args:
        parser.error("no argument is allowed - use option --help to get an help screen")

    options.loglevel = getattr(logging, options.loglevel.upper(), logging.INFO)
    if options.audit_file is not None and not options.audit_file.strip():
        parser.error('--audit-file must not be empty')
    if options.audit_max_bytes < 65536 or not 1 <= options.audit_files <= 100:
        parser.error('Audit retention requires at least 65536 bytes and 1..100 files')

    if getattr(options, 'subcommand') \
       and options.subcommand in _SUBCMDS:
        _SUBCMDS[options.subcommand].valid_subcmd_parser(parser, options)
    else:
        parser.error("too few arguments - use option --help to get an help screen")

    return options


class MonitDockerExit(SystemExit):
    pass


def _audit_journal(options, explicit=False):
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


def _read_audit_token(path):
    import re
    try:
        with open(path) as stream:
            token = stream.read(67)
        if not re.fullmatch('[0-9a-f]{64}\n?', token):
            raise ValueError('Invalid token')
        return token.strip()
    except (OSError, ValueError, UnicodeError) as error:
        raise MonitoringError(110, 'Audit token file must contain a 64-character hex secret') from error


class MonitDockerSubCmdAudit:
    CMD_NAME = 'audit-export'
    CMD_HELP = 'export retained actions and notifications without connecting to Docker'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--format', choices=('jsonl', 'csv'), default='jsonl')
        parser.add_argument('--category', choices=('action', 'notification'))
        parser.add_argument('--since', help='UTC/RFC3339 timestamp; export events at or after this time')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        from datetime import datetime
        if not options.audit_file:
            parser.error('--audit-file is required for audit commands')
        if options.since:
            try:
                options.since = datetime.fromisoformat(options.since.replace('Z', '+00:00'))
                if options.since.tzinfo is None:
                    raise ValueError('Timezone required')
            except ValueError:
                parser.error('--since must be an ISO timestamp with timezone')

    def records(self):
        from datetime import datetime
        records = _audit_journal(self.options, explicit=True).read()
        return [record for record in records
                if (not self.options.category or record.get('category') == self.options.category)
                and (not self.options.since or datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')) >= self.options.since)]

    def __call__(self):
        from monit_docker.audit import export_events
        export_events(self.records(), sys.stdout, self.options.format)
        return 0


class MonitDockerSubCmdAuditSend(MonitDockerSubCmdAudit):
    CMD_NAME = 'audit-send'
    CMD_HELP = 'forward retained audit events to an HTTPS service; keep the local journal'

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = super().load_subcmd_parser(subparsers)
        parser.add_argument('--url', required=True, help='HTTPS JSON event receiver')
        parser.add_argument('--token-file', help='optional Bearer secret file')

    def __call__(self):
        from monit_docker.audit_forward import send_events
        count = send_events(self.records(), self.options.url, self.options.token_file)
        print('%d audit events acknowledged; local journal retained' % count, file=sys.stderr)
        return 0


class MonitDockerSubCmdRestartReset(object):
    CMD_NAME = 'restart-reset'
    CMD_HELP = 'rearm automatic restarts for one exact container ID without connecting to Docker'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--state-file', required=True)
        parser.add_argument('--container-id', required=True, help='full 64-character Docker container ID')

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        import re
        if not options.state_file.strip():
            parser.error('--state-file must not be empty')
        if not re.fullmatch('[0-9a-f]{64}', options.container_id):
            parser.error('--container-id must be a full 64-character lowercase hexadecimal ID')

    def __call__(self):
        import uuid
        from monit_docker.adapters.state import LocalState
        with LocalState(self.options.state_file) as state:
            key = restart_key(self.options.container_id)
            if key not in state.restarts:
                raise MonitoringError(110, 'no restart attempts recorded for this container')
            audit = _audit_journal(self.options)
            fields = dict(correlation_id=uuid.uuid4().hex, source='manual', actor=getpass.getuser(),
                          container_id=self.options.container_id, action='restart-reset')
            audit.record('action', 'started', result='pending', **fields)
            try:
                state.reset_restarts(key)
            except Exception as error:
                audit.finish('action', 'completed', result='failed', reason=type(error).__name__,
                             error_code=getattr(error, 'code', None), **fields)
                raise
            audit.finish('action', 'completed', result='succeeded', **fields)
        print(json.dumps(dict(container_id=self.options.container_id, status='rearmed')))
        return 0


class MonitDockerSubCmdStats(object):
    CMD_NAME = 'stats'
    CMD_HELP = 'display stats information'
    USE_RULES = False

    def __init__(self, options):
        self.options = options
        config = Configuration(options.conffile, MONIT_DOCKER_CONFIG).load(
            include_rules=self.USE_RULES)
        selector = ContainerSelector(
            selectors=dict((kind, getattr(options, kind)) for kind in ('id', 'name', 'label', 'image')),
            statuses=options.status, groups=config.get('ctn-groups'), selected_groups=options.ctn_grp)
        self.rules = ()
        if self.USE_RULES:
            parser = RuleParser(config.get('commands'), config.get('conditions'), config.get('dir-groups'))
            self.rules = tuple(parser.parse(expression) for expression in options.cmd)
        collector = DockerCollector(client_factory(config, options.client, options.client_from_env),
                                    selector, config.get('dir-groups'))
        for resource in options.resource or ():
            filesystem = filesystem_resource(resource)
            if filesystem and filesystem[1] not in collector.dir_groups:
                raise MonitoringError(110, 'unknown directory group: %s' % filesystem[1])
        self.audit = None
        if self.rules or getattr(options, 'allow_actions', False) or getattr(options, 'notification_token_file', None) or getattr(options, 'audit_read_token_file', None):
            self.audit = _audit_journal(options)
        source = 'manual' if options.subcommand == 'monit' else 'automatic'
        actor = getpass.getuser() if source == 'manual' else 'cron' if options.subcommand == 'cron' else 'rule-engine'
        self.engine = MonitoringEngine(collector, DockerActionExecutor(collector), audit=self.audit,
                                       audit_source=source, audit_actor=actor)

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME,
                                       help = cls.CMD_HELP)
        parser.add_argument("--output",
                            dest    = 'output',
                            default = 'json',
                            choices = ('text', 'json'),
                            help    = "formatting style for command output")
        parser.add_argument("--rsc",
                            action  = 'append',
                            dest    = 'resource',
                            default = [],
                            type    = _resource_argument,
                            help    = "resource information")

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not options.resource:
            options.resource = RESOURCE_CHOICES

    def _display_value(self, resource, value):
        return value if self.CMD_NAME == 'monit' else format_resource(resource, value)

    def _output_snapshot(self, snapshot):
        if self.options.output == 'json':
            values = dict((resource, self._display_value(resource, snapshot.resource_value(resource)))
                          for resource in self.options.resource)
            sys.stdout.write(json.dumps({snapshot.name: values}) + '\n')
        else:
            values = [snapshot.name]
            for resource in self.options.resource:
                value = self._display_value(resource, snapshot.resource_value(resource))
                if value is None or (resource == 'pid' and not value):
                    value = 'null'
                values.append('%s:%s' % (resource, value))
            sys.stdout.write('|'.join(values) + '\n')

    def __call__(self):
        dry_run = getattr(self.options, 'dry_run', False)
        return self.engine.run_once(rules=self.rules, resources=self.options.resource,
                                    on_snapshot=None if self.rules else self._output_snapshot,
                                    dry_run=dry_run,
                                    on_action=self._output_action if dry_run else None)

    @staticmethod
    def _output_action(decision):
        sys.stdout.write(json.dumps(decision._asdict()) + '\n')


class MonitDockerSubCmdMonit(MonitDockerSubCmdStats):
    CMD_NAME = 'monit'
    CMD_HELP = 'return stats information with return code'
    USE_RULES = True
    ALLOW_RESOURCE_QUERY = True

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME,
                                       help = cls.CMD_HELP)
        parser.set_defaults(resource=[])
        if cls.ALLOW_RESOURCE_QUERY:
            parser.add_argument("--rsc", action='append', dest='resource',
                                type=_resource_argument, help='resource information')
        parser.add_argument("--cmd",
                            "--cmd-if",
                            action  = 'append',
                            dest    = 'cmd',
                            default = [],
                            help    = "run docker command or execute command inside containers")
        parser.add_argument("--propagate-exit-code",
                            action  = 'store_true',
                            default = False,
                            help    = "return the first failed container exec's exit code instead of 116")
        parser.add_argument('--dry-run', action='store_true',
                            help='show matching actions as JSON without executing them')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if options.resource and options.cmd:
            parser.error("rsc and cmd options can't be in the same command")
        if options.propagate_exit_code and not options.cmd:
            parser.error("--propagate-exit-code requires --cmd or --cmd-if")
        if options.dry_run and not options.cmd:
            parser.error('--dry-run requires --cmd or --cmd-if')

        setattr(options, 'output', 'text')

    @staticmethod
    def _get_status_rc(status):
        status = status.lower()
        if status not in STATUS_RC:
            raise ValueError("unknown status: %r" % status)

        return STATUS_RC[status]

    def _write_pidfile(self, pid, container_name):
        if not self.options.runtimedir:
            LOG.warning("runtime directory not configured or doesn't exist")
            return

        helpers.file_w_tmp((str(pid) + '\n',),
                           os.path.join(self.options.runtimedir, "%s.pid" % container_name))

    def _output_snapshot(self, snapshot):
        resources = self.options.resource
        if len(resources) == 1:
            resource = resources[0]
            if resource == 'status':
                raise MonitDockerExit(self._get_status_rc(snapshot.status))
            if resource == 'health':
                raise MonitDockerExit(HEALTH_RC.get(snapshot.health, HEALTH_RC['unknown']))
            if resource == 'pid':
                self._write_pidfile(snapshot.pid or '', snapshot.name)
            if resource.endswith('_percent'):
                value = getattr(snapshot, resource)
                if value is None:
                    raise MonitoringError(115, 'no statistic for the container: %s' % snapshot.name)
                raise MonitDockerExit(max(0, min(100, int(value))))
        if not resources and snapshot.status not in ('paused', 'running'):
            return
        super(MonitDockerSubCmdMonit, self)._output_snapshot(snapshot)


def _add_policy_options(parser):
    parser.add_argument('--max-restarts', type=int, default=DEFAULT_MAX_RESTARTS,
                        help='automatic restart attempts per container until explicit rearm (default: %(default)s)')
    parser.add_argument('--trigger-after', type=float, default=0,
                        help='seconds a condition must remain observed true before acting (default: 0)')
    parser.add_argument('--max-gap', type=float,
                        help='maximum seconds between true observations; required with --trigger-after')


def _validate_policy_options(parser, options):
    if options.max_restarts < 1:
        parser.error('--max-restarts must be a positive integer')
    if not math.isfinite(options.trigger_after) or options.trigger_after < 0:
        parser.error('--trigger-after must be finite and non-negative')
    if options.trigger_after > 0:
        if not options.cmd:
            parser.error('--trigger-after requires --cmd or --cmd-if')
        if options.max_gap is None or not math.isfinite(options.max_gap) or options.max_gap <= 0:
            parser.error('--trigger-after requires a finite positive --max-gap')
    elif options.max_gap is not None:
        parser.error('--max-gap requires a positive --trigger-after')


def _rule_policy(state, rules, options):
    if not rules:
        return CooldownPolicy(state, rules, options.cooldown)
    return RestartPolicy(state, rules, options.cooldown, options.trigger_after,
                         options.max_gap, read_only=options.dry_run, max_restarts=options.max_restarts)


class MonitDockerSubCmdCron(MonitDockerSubCmdMonit):
    CMD_NAME = 'cron'
    CMD_HELP = 'run one locked monitoring cycle with persistent action cooldowns'
    ALLOW_RESOURCE_QUERY = False

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = super(MonitDockerSubCmdCron, cls).load_subcmd_parser(subparsers)
        parser.add_argument('--state-file', required=True,
                            help='persistent JSON state; use one file per Docker host and job')
        parser.add_argument('--cooldown', type=float, default=300,
                            help='minimum seconds between attempts of the same rule (default: 300)')
        _add_policy_options(parser)

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        super(MonitDockerSubCmdCron, cls).valid_subcmd_parser(parser, options)
        if not options.cmd:
            parser.error('cron requires --cmd or --cmd-if')
        if not options.state_file.strip():
            parser.error('--state-file must not be empty')
        if not math.isfinite(options.cooldown) or options.cooldown < 0:
            parser.error('--cooldown must be a finite non-negative number')
        _validate_policy_options(parser, options)

    def __call__(self):
        from monit_docker.adapters.state import LocalState
        with LocalState(self.options.state_file) as state:
            policy = _rule_policy(state, self.rules, self.options)
            return self.engine.run_once(rules=self.rules, resources=(),
                                        dry_run=self.options.dry_run, action_policy=policy,
                                        on_action=self._output_action)


class MonitDockerSubCmdServe(MonitDockerSubCmdStats):
    CMD_NAME = 'serve'
    CMD_HELP = 'monitor continuously and expose cached status and Prometheus metrics'
    USE_RULES = True

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--bind', default='127.0.0.1', help='IPv4 listen address (default: 127.0.0.1)')
        parser.add_argument('--port', type=int, default=9808, help='HTTP port (default: 9808)')
        parser.add_argument('--interval', type=float, default=30, help='seconds to wait after each cycle (default: 30)')
        parser.add_argument('--stale-after', type=float, default=None, help='maximum cache age in seconds (default: max(90, 3 * interval))')
        parser.add_argument('--rsc', action='append', dest='resource', default=[], type=_resource_argument)
        parser.add_argument('--cmd', '--cmd-if', action='append', default=[], help='optional remediation rule')
        parser.add_argument('--state-file', help='persistent cooldown state; required with rules')
        parser.add_argument('--cooldown', type=float, default=300, help='seconds between attempts of the same rule (default: 300)')
        parser.add_argument('--dry-run', action='store_true', help='evaluate remediation rules without executing them')
        parser.add_argument('--allow-actions', action='store_true', help='enable the authenticated manual action API')
        parser.add_argument('--action-origin', help='exact HTTPS browser origin allowed to submit actions')
        parser.add_argument('--action-token-file', help='file containing a 64-character hex proxy secret')
        parser.add_argument('--trust-proxy-user', action='store_true',
                            help='require X-Monit-Actor supplied and overwritten by the authenticated proxy')
        parser.add_argument('--audit-read-token-file', help='enable private journal reads through an authenticated proxy with a separate secret')
        parser.add_argument('--notification-token-file', help='enable private Alertmanager audit webhook with a separate Bearer secret')
        parser.add_argument('--action-cooldown', type=float, default=30,
                            help='minimum seconds between manual attempts per container (default: 30)')
        _add_policy_options(parser)

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        import ipaddress
        try:
            ipaddress.IPv4Address(options.bind)
        except ValueError:
            parser.error('--bind must be an IPv4 address')
        if not 1 <= options.port <= 65535:
            parser.error('--port must be between 1 and 65535')
        if not math.isfinite(options.interval) or options.interval < 0.1:
            parser.error('--interval must be finite and at least 0.1 seconds')
        if options.stale_after is None:
            options.stale_after = max(90, 3 * options.interval)
        if not math.isfinite(options.stale_after) or options.stale_after < options.interval:
            parser.error('--stale-after must be finite and at least --interval')
        if not math.isfinite(options.cooldown) or options.cooldown < 0:
            parser.error('--cooldown must be finite and non-negative')
        if options.cmd and not options.state_file:
            parser.error('serve with rules requires --state-file')
        if options.state_file is not None and not options.state_file.strip():
            parser.error('--state-file must not be empty')
        if options.dry_run and not options.cmd:
            parser.error('--dry-run requires --cmd or --cmd-if')
        if options.allow_actions:
            from urllib.parse import urlsplit
            try:
                origin = urlsplit(options.action_origin or '')
                valid_origin = (origin.scheme == 'https' and origin.hostname
                                and not origin.username and not origin.password
                                and not origin.path and not origin.query and not origin.fragment
                                and not any(c.isspace() for c in options.action_origin)
                                and (origin.port is None or 1 <= origin.port <= 65535))
            except ValueError:
                valid_origin = False
            if not valid_origin:
                parser.error('--allow-actions requires --action-origin https://host[:port] without a path')
            if not options.action_token_file or not options.state_file:
                parser.error('--allow-actions requires --action-token-file and --state-file')
            if options.dry_run:
                parser.error('--allow-actions cannot be combined with --dry-run')
        elif options.action_origin or options.action_token_file:
            parser.error('--action-origin and --action-token-file require --allow-actions')
        if not math.isfinite(options.action_cooldown) or options.action_cooldown < 1:
            parser.error('--action-cooldown must be finite and at least 1 second')
        if options.audit_read_token_file and not options.audit_file:
            parser.error('--audit-read-token-file requires an explicit --audit-file')
        if options.trust_proxy_user and not options.allow_actions:
            parser.error('--trust-proxy-user requires --allow-actions')
        _validate_policy_options(parser, options)
        if options.trigger_after and options.max_gap <= options.interval:
            parser.error('--max-gap must exceed --interval to allow time for collection')
        options.resource = options.resource or RESOURCE_CHOICES

    def _cycle(self, observer):
        def run(policy=None):
            return self.engine.run_once(rules=self.rules, resources=self.options.resource,
                                        dry_run=self.options.dry_run, action_policy=policy,
                                        on_action=observer)
        try:
            if self.options.state_file:
                from monit_docker.adapters.state import LocalState
                with LocalState(self.options.state_file) as state:
                    return run(_rule_policy(state, self.rules, self.options))
            return run()
        except APIError as error:
            raise MonitoringError(180, str(error))
        except DockerException as error:
            raise MonitoringError(170, str(error))

    def __call__(self):
        from monit_docker.service import MonitorService
        from monit_docker.adapters.http import run_server
        actions = None
        if self.options.allow_actions:
            import re
            from monit_docker.manual_actions import ManualActions
            try:
                with open(self.options.action_token_file) as stream:
                    raw_token = stream.read(67)
                if not re.fullmatch('[0-9a-f]{64}\n?', raw_token):
                    raise ValueError('invalid secret')
                token = raw_token.rstrip('\n')
            except (OSError, UnicodeError, ValueError):
                raise MonitoringError(110, 'action token file must contain a 64-character hex secret')
            actions = ManualActions(self._manual_action, self.options.action_origin, token,
                                    audit=self.audit, trust_actor=self.options.trust_proxy_user)
        notifications = None
        if self.options.notification_token_file:
            from monit_docker.notification_audit import NotificationAudit
            notifications = NotificationAudit(self.audit, _read_audit_token(self.options.notification_token_file))
        reader = None
        if self.options.audit_read_token_file:
            from monit_docker.audit_query import AuditReader
            read_token = _read_audit_token(self.options.audit_read_token_file)
            if (actions and read_token == actions.token) or (notifications and read_token == notifications.token):
                raise MonitoringError(110, 'audit read secret must differ from action and notification secrets')
            reader = AuditReader(self.audit, read_token)
        monitor = MonitorService(self._cycle, self.options.interval, self.options.stale_after,
                                 manual_actions=actions, notification_audit=notifications, audit_reader=reader)
        run_server(monitor, self.options.bind, self.options.port)

    def _manual_action(self, container_id, command):
        import hashlib
        import time
        from monit_docker.adapters.state import LocalState
        try:
            with LocalState(self.options.state_file) as state:
                def claim(identifier):
                    if command == 'restart-reset' and restart_key(identifier) not in state.restarts:
                        raise ActionRejected('no_restart_attempts')
                    key = hashlib.sha256(('manual:' + identifier).encode('ascii')).hexdigest()
                    return state.reserve(key, time.time(), self.options.action_cooldown)
                self.engine.run_manual_action(container_id, command, claim,
                                             reset_restarts=lambda identifier: state.reset_restarts(restart_key(identifier)))
        except APIError as error:
            raise MonitoringError(180, str(error))
        except DockerException as error:
            raise MonitoringError(170, str(error))


class MonitDockerSubCmdCheckConfig(object):
    CMD_NAME = 'check-config'
    CMD_HELP = 'validate configuration and rules without connecting to Docker'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--output', choices=('text', 'json'), default='text')
        parser.add_argument('--cmd', '--cmd-if', action='append', dest='cmd', default=[],
                            help='validate an additional rule without executing it')

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        pass

    def __call__(self):
        from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
        result = {'valid': False, 'errors': [], 'summary': {}}
        try:
            result['summary'] = check_configuration(
                self.options.conffile, MONIT_DOCKER_CONFIG,
                selectors=dict((kind, getattr(self.options, kind)) for kind in ('id', 'name', 'label', 'image')),
                selected_groups=self.options.ctn_grp, client=self.options.client,
                from_env=self.options.client_from_env, expressions=self.options.cmd)
            result['valid'] = True
        except ConfigurationCheckError as error:
            result['errors'].append({'location': error.location, 'message': str(error)})
        if self.options.output == 'json':
            sys.stdout.write(json.dumps(result) + '\n')
        elif result['valid']:
            sys.stdout.write('Configuration valid: %s\n' % ', '.join(
                '%s=%s' % (name, result['summary'][name]) for name in sorted(result['summary'])))
        else:
            for error in result['errors']:
                sys.stdout.write('Configuration invalid: %s: %s\n' % (error['location'], error['message']))
        return 0 if result['valid'] else 110


_SUBCMDS['audit-export'] = MonitDockerSubCmdAudit
_SUBCMDS['audit-send'] = MonitDockerSubCmdAuditSend
_SUBCMDS['restart-reset'] = MonitDockerSubCmdRestartReset
_SUBCMDS['check-config'] = MonitDockerSubCmdCheckConfig
_SUBCMDS['serve'] = MonitDockerSubCmdServe
_SUBCMDS['cron'] = MonitDockerSubCmdCron
_SUBCMDS['monit'] = MonitDockerSubCmdMonit
_SUBCMDS['stats'] = MonitDockerSubCmdStats


def main(options):
    """
    Main function
    """
    # Offline commands do not require logging setup, runtime directories or Docker.
    if options.subcommand in ('check-config', 'audit-export', 'audit-send', 'restart-reset'):
        try:
            return _SUBCMDS[options.subcommand](options)()
        except (MonitoringError, OSError, ValueError) as error:
            print('%s operation failed (%s)' % (options.subcommand, type(error).__name__), file=sys.stderr)
            return getattr(error, 'code', 119)

    xformat     = "%(levelname)s:%(asctime)-15s: %(message)s"
    datefmt     = '%Y-%m-%d %H:%M:%S'
    logging.basicConfig(level   = options.loglevel,
                        format  = xformat,
                        datefmt = datefmt)

    if os.path.isdir(os.path.dirname(options.logfile)):
        filehandler = WatchedFileHandler(options.logfile)
        filehandler.setFormatter(logging.Formatter(xformat,
                                                   datefmt = datefmt))
        root_logger = logging.getLogger('')
        root_logger.addHandler(filehandler)

    if options.runtimedir and not os.path.isdir(options.runtimedir):
        try:
            helpers.make_dirs(options.runtimedir)
        except Exception:
            LOG.warning("unable to create runtime directory: %r", options.runtimedir)
            setattr(options, 'runtimedir', None)

    rc           = 0
    try:
        monit_docker = _SUBCMDS[options.subcommand](options)
        monit_docker()
    except APIError as e:
        rc = 180
        LOG.error(e.explanation)
    except DockerException as e:
        rc = 170
        LOG.error(e)
    except CommandExecutionError as e:
        rc = e.exit_code if getattr(options, 'propagate_exit_code', False) else e.code
        LOG.error(e)
    except MonitoringError as e:
        rc = e.code
        LOG.error(e)
    except MonitDockerExit as e:
        rc = e.code
    except (SystemExit, KeyboardInterrupt):
        rc = 255
    except SyntaxError as e:
        rc = 140
        LOG.error(e)
    except Exception as e:
        rc = 150
        LOG.exception(e)

    return rc


if __name__ == '__main__':
    sys.exit(main(argv_parse_check()))
