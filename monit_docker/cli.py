#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2019-2026 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy CLI: argument parsing, composition, output and process exit codes."""

from __future__ import absolute_import

import argparse

from dwho.cli import write_json
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

from monit_docker.composition import build_application, audit_journal as _audit_journal
from monit_docker.observation import DEFAULT_REFRESH, MIN_REFRESH, MAX_REFRESH
from monit_docker.job import JobOptions, validate_job
from dataclasses import replace
from monit_docker.audit import DEFAULT_MAX_BYTES
from monit_docker.adapters.syntax import RESOURCE_CHOICES, DEFAULT_RESOURCE_CHOICES, STATUS_RC, HEALTH_RC
from monit_docker.domain.runtime import DEFAULT_EVENT_WINDOW, valid_event_window
from monit_docker.core.policy import DEFAULT_MAX_RESTARTS
from monit_docker.domain.maintenance import MAX_MAINTENANCE_SECONDS
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


def job_options(options):
    from dataclasses import fields
    values = {field.name: getattr(options, field.name, field.default) for field in fields(JobOptions)}
    values.update(notifications_enabled=bool(getattr(options, 'notification_token_file', None)),
                  audit_read_enabled=bool(getattr(options, 'audit_read_token_file', None)))
    return JobOptions(**values)


def _resource_argument(value):
    if value not in RESOURCE_CHOICES and not filesystem_resource(value):
        raise argparse.ArgumentTypeError('unknown resource; filesystem resources use disk_percent[group] or inode_percent[group]')
    return value

def argv_parse_check(argv=None, parser_class=argparse.ArgumentParser):
    """
    Parse (and check a little) command line parameters
    """
    parser        = parser_class()

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

    parser.add_argument('--event-window', type=int, default=DEFAULT_EVENT_WINDOW,
                        help='seconds of Docker history for OOM/start checks (1..86400; default: 300)')
    parser.add_argument('--audit-file', default=os.environ.get('MONIT_DOCKER_AUDIT_FILE'),
                        help='persistent event journal (default: audit/events.jsonl beside state file, otherwise user state directory)')
    parser.add_argument('--audit-max-bytes', type=int, default=DEFAULT_MAX_BYTES, help='maximum bytes per audit file (default: 5 MiB)')
    parser.add_argument('--audit-files', type=int, default=5, help='total retained audit files including active file (default: 5)')
    subparsers    = parser.add_subparsers(dest = 'subcommand',
                                          help = "choice sub-command")

    for subcmd in six.itervalues(_SUBCMDS):
        subcmd.load_subcmd_parser(subparsers)

    options, args = parser.parse_known_args(argv)

    if args:
        parser.error("no argument is allowed - use option --help to get an help screen")

    options.loglevel = getattr(logging, options.loglevel.upper(), logging.INFO)
    if not valid_event_window(options.event_window):
        parser.error('--event-window must be between 1 and 86400 seconds')
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

    def filtered_records(self, records):
        from datetime import datetime
        return (record for record in records
                if (not self.options.category or record.get('category') == self.options.category)
                and (not self.options.since or datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')) >= self.options.since))

    def records(self):
        # HTTPS forwarding retains its existing materialized batch contract.
        return list(self.filtered_records(_audit_journal(self.options, explicit=True).read()))

    def __call__(self):
        from monit_docker.audit import export_events
        with _audit_journal(self.options, explicit=True).iter_records() as records:
            export_events(self.filtered_records(records), sys.stdout, self.options.format)
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


class MonitDockerSubCmdAuditMigrate:
    CMD_NAME = 'audit-migrate'
    CMD_HELP = 'validate or copy retained journals to schema 2 with an original-byte backup'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--dry-run', action='store_true', help='validate and report without writing output (default)')
        mode.add_argument('--apply', action='store_true', help='create a verified migration bundle; never replace source files')
        parser.add_argument('--output-dir', help='new directory for backup, converted journals and manifest')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not options.audit_file:
            parser.error('--audit-file is required for audit commands')
        if options.apply and not options.output_dir:
            parser.error('--apply requires --output-dir')
        if options.output_dir is not None and not options.output_dir.strip():
            parser.error('--output-dir must not be empty')

    def __call__(self):
        from monit_docker.audit import AuditError
        from monit_docker.audit_migrate import migrate_journal
        try:
            report = migrate_journal(_audit_journal(self.options, explicit=True),
                                     self.options.output_dir, self.options.apply)
        except AuditError as error:
            print('audit-migrate: %s' % error, file=sys.stderr)
            return error.code
        write_json(report, ensure_ascii=True, sort_keys=True)
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
        from monit_docker.application_state import StateOperations
        from monit_docker.adapters.state import LocalState
        operations = StateOperations(LocalState, _audit_journal(self.options))
        result = operations.reset_restarts(self.options.state_file, self.options.container_id,
                                           getpass.getuser())
        write_json(result)
        return 0


class MonitDockerSubCmdMaintenance(MonitDockerSubCmdRestartReset):
    CMD_NAME = 'maintenance'
    CMD_HELP = 'suspend automatic actions for one exact container ID; duration 0 resumes'

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--state-file', required=True)
        parser.add_argument('--container-id', required=True, help='full Docker container ID')
        parser.add_argument('--duration', required=True, type=int, help='seconds, 1..86400; 0 resumes early')

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        super().valid_subcmd_parser(parser, options)
        if not 0 <= options.duration <= MAX_MAINTENANCE_SECONDS:
            parser.error('--duration must be between 0 and 86400 seconds')

    def __call__(self):
        from monit_docker.application_state import StateOperations
        from monit_docker.adapters.state import LocalState
        operations = StateOperations(LocalState, _audit_journal(self.options))
        result = operations.maintenance(self.options.state_file, self.options.container_id,
                                        self.options.duration, getpass.getuser())
        write_json(result)
        return 0


class MonitDockerSubCmdStats(object):
    CMD_NAME = 'stats'
    CMD_HELP = 'display stats information'
    USE_RULES = False

    def __init__(self, options):
        self.options = options
        self.application = build_application(job_options(options),
            config=getattr(options, '_scenario_config', None), inline=MONIT_DOCKER_CONFIG,
            use_rules=self.USE_RULES, actor=getpass.getuser())
        self.engine, self.rules, self.audit = self.application.engine, self.application.rules, self.application.audit


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
            options.resource = DEFAULT_RESOURCE_CHOICES

    def _display_value(self, resource, value):
        return value if self.CMD_NAME == 'monit' else format_resource(resource, value)

    def _output_snapshot(self, snapshot):
        if self.options.output == 'json':
            values = dict((resource, self._display_value(resource, snapshot.resource_value(resource)))
                          for resource in self.options.resource)
            write_json({snapshot.name: values})
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
        write_json(decision._asdict())


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
        return self.application.run_once(on_action=self._output_action)


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
        parser.add_argument('--allow-maintenance', action='store_true',
                            help='also enable authenticated per-container maintenance controls')
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
        if options.allow_maintenance and not options.allow_actions:
            parser.error('--allow-maintenance requires --allow-actions')
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
        options.resource = options.resource or DEFAULT_RESOURCE_CHOICES


    def __call__(self):
        from monit_docker.service import MonitorService
        from monit_docker.adapters.http import run_server
        from monit_docker.adapters.http_security import load_security
        from monit_docker.manual_actions import ManualActions
        from monit_docker.notification_audit import NotificationAudit
        from monit_docker.audit_query import AuditReader
        security = load_security(
            action_token_file=self.options.action_token_file if self.options.allow_actions else None,
            action_origin=self.options.action_origin, trust_actor=self.options.trust_proxy_user,
            notification_token_file=self.options.notification_token_file,
            audit_read_token_file=self.options.audit_read_token_file)
        actions = (ManualActions(self.application.manual_action, audit=self.audit,
                                 allow_maintenance=self.options.allow_maintenance)
                   if self.options.allow_actions else None)
        notifications = NotificationAudit(self.audit) if security.notification_token else None
        reader = AuditReader(self.audit) if security.audit_token else None
        monitor = MonitorService(self.application.cycle, self.options.interval, self.options.stale_after,
                                 manual_actions=actions, notification_audit=notifications, audit_reader=reader)
        run_server(monitor, self.options.bind, self.options.port, security=security)


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
            write_json(result)
        elif result['valid']:
            sys.stdout.write('Configuration valid: %s\n' % ', '.join(
                '%s=%s' % (name, result['summary'][name]) for name in sorted(result['summary'])))
        else:
            for error in result['errors']:
                sys.stdout.write('Configuration invalid: %s: %s\n' % (error['location'], error['message']))
        return 0 if result['valid'] else 110


class MonitDockerSubCmdRun(object):
    CMD_NAME = 'run'
    CMD_HELP = 'run a named monitoring scenario from configuration'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('scenario', help='exact scenario name')
        parser.add_argument('--dry-run', action='store_true',
                            help='simulate matching actions using the existing policy state')

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if any(getattr(options, field) for field in ('name','id','image','label','ctn_grp','status')) or options.client or options.client_from_env:
            parser.error('put container selection and Docker client settings in the scenario')

    def __call__(self):
        return run_scenario(self.options, MONIT_DOCKER_CONFIG)


class MonitDockerSubCmdScenario(MonitDockerSubCmdRun):
    CMD_NAME = 'scenario'
    CMD_HELP = 'list or inspect named scenarios without connecting to Docker'

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('operation', choices=('list', 'show'))
        parser.add_argument('scenario', nargs='?', help='exact scenario name for show')

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        super(MonitDockerSubCmdScenario, cls).valid_subcmd_parser(parser, options)
        if (options.operation == 'show') != bool(options.scenario):
            parser.error('scenario show requires a name; scenario list takes no name')

    def __call__(self):
        return inspect_scenarios(self.options, MONIT_DOCKER_CONFIG)


class MonitDockerSubCmdTui:
    CMD_NAME = 'tui'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help='explicit read-only terminal interface')
        parser.add_argument('--refresh', type=int, default=DEFAULT_REFRESH, help='refresh interval in seconds (5..3600)')
        parser.add_argument('--rsc', action='append', dest='resource', default=[], type=_resource_argument)

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not MIN_REFRESH <= options.refresh <= MAX_REFRESH:
            parser.error('--refresh must be between 5 and 3600 seconds')
        if not options.resource:
            options.resource = DEFAULT_RESOURCE_CHOICES

    def __call__(self):
        from monit_docker.tui import run
        from monit_docker.observation import Observation
        from monit_docker.audit_query import AuditReader

        def create_observation():
            job = replace(job_options(self.options), subcommand='stats')
            application = build_application(job, inline=MONIT_DOCKER_CONFIG, use_rules=False)
            reader = AuditReader(_audit_journal(job)) if job.audit_file else None
            return Observation(application.run_once, reader, self.options.refresh)
        return run(create_observation)


_SUBCMDS['tui'] = MonitDockerSubCmdTui
_SUBCMDS['run'] = MonitDockerSubCmdRun
_SUBCMDS['scenario'] = MonitDockerSubCmdScenario
_SUBCMDS['audit-export'] = MonitDockerSubCmdAudit
_SUBCMDS['audit-send'] = MonitDockerSubCmdAuditSend
_SUBCMDS['audit-migrate'] = MonitDockerSubCmdAuditMigrate
_SUBCMDS['maintenance'] = MonitDockerSubCmdMaintenance
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
    # Resolve named jobs before setup; offline commands need no logging or Docker.
    if options.subcommand in ('tui', 'check-config', 'audit-export', 'audit-send', 'audit-migrate', 'restart-reset', 'maintenance', 'run', 'scenario'):
        try:
            return _SUBCMDS[options.subcommand](options)()
        except (MonitoringError, OSError, ValueError) as error:
            print('%s operation failed (%s)' % (options.subcommand, type(error).__name__), file=sys.stderr)
            return getattr(error, 'code', 119)

    initialize_runtime(options)
    return run_operation(lambda: _SUBCMDS[options.subcommand](options)(), options)


def initialize_runtime(options):
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


def run_operation(operation, options):
    rc           = 0
    try:
        operation()
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


def scenario_output(snapshot, options):
    values = {resource: format_resource(resource, snapshot.resource_value(resource)) for resource in options.resource}
    if options.output == 'json':
        write_json({snapshot.name: values})
    else:
        print('|'.join([snapshot.name] + ['%s:%s' % (key, 'null' if value is None or key == 'pid' and not value else value)
                                         for key, value in values.items()]))


def run_scenario(outer, inline=None):
    from monit_docker.adapters.validation import CheckedConfiguration, ConfigurationCheckError, validate_configuration
    from monit_docker.adapters.scenarios import validate_scenario
    from monit_docker.adapters.http import run_server
    from monit_docker.service import MonitorService
    try:
        config = CheckedConfiguration(outer.conffile, inline).load()
        validate_configuration(config)
        job = validate_scenario(config, outer.scenario)
        if outer.dry_run and not job.cmd:
            raise ConfigurationCheckError('scenarios.' + outer.scenario, '--dry-run requires a scenario with rules')
        inherited = {field.replace('-', '_'): getattr(outer, field.replace('-', '_'))
                     for field in ('audit-file','audit-max-bytes','audit-files','event-window')
                     if field not in config['scenarios'][outer.scenario]}
        job = validate_job(replace(job, conffile=outer.conffile, dry_run=job.dry_run or outer.dry_run, **inherited))
    except (ConfigurationCheckError, ValueError) as error:
        print('Invalid scenario configuration: %s: %s' % (getattr(error, 'location', 'options'), error), file=sys.stderr)
        return 110
    initialize_runtime(outer)
    def operation():
        app = build_application(job, config=config, use_rules=job.subcommand != 'stats')
        if job.subcommand == 'serve':
            run_server(MonitorService(app.cycle, job.interval, job.stale_after), job.bind, job.port)
        else:
            app.run_once(on_snapshot=lambda snapshot: scenario_output(snapshot, job),
                         on_action=MonitDockerSubCmdStats._output_action)
    return run_operation(operation, outer)


def inspect_scenarios(options, inline=None):
    from monit_docker.adapters.validation import CheckedConfiguration, ConfigurationCheckError, validate_configuration
    from monit_docker.adapters.scenarios import validate_scenario
    try:
        config = CheckedConfiguration(options.conffile, inline).load()
        validate_configuration(config)
        if options.operation == 'show':
            validate_scenario(config, options.scenario)
            write_json(config['scenarios'][options.scenario], indent=2)
        else:
            entries = [dict(name=name, mode=validate_scenario(config, name).subcommand, description=entry.get('description',''))
                       for name, entry in sorted(config.get('scenarios',{}).items())]
            write_json(entries, indent=2)
    except ConfigurationCheckError as error:
        print('Invalid scenario configuration: %s: %s' % (error.location, error), file=sys.stderr)
        return 110
    return 0


if __name__ == '__main__':
    sys.exit(main(argv_parse_check()))

