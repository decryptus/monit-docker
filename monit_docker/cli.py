#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2019-2022 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy CLI: argument parsing, composition, output and process exit codes."""

from __future__ import absolute_import

import argparse
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

from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor, client_factory
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.syntax import RESOURCE_CHOICES, STATUS_RC
from monit_docker.core import MonitoringEngine
from monit_docker.core.policy import CooldownPolicy
from monit_docker.domain.errors import CommandExecutionError, MonitoringError
from monit_docker.outputs.formatting import format_resource

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

    subparsers    = parser.add_subparsers(dest = 'subcommand',
                                          help = "choice sub-command")

    for subcmd in six.itervalues(_SUBCMDS):
        subcmd.load_subcmd_parser(subparsers)

    options, args = parser.parse_known_args()

    if args:
        parser.error("no argument is allowed - use option --help to get an help screen")

    options.loglevel = getattr(logging, options.loglevel.upper(), logging.INFO)

    if getattr(options, 'subcommand') \
       and options.subcommand in _SUBCMDS:
        _SUBCMDS[options.subcommand].valid_subcmd_parser(parser, options)
    else:
        parser.error("too few arguments - use option --help to get an help screen")

    return options


class MonitDockerExit(SystemExit):
    pass


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
            parser = RuleParser(config.get('commands'), config.get('conditions'))
            self.rules = tuple(parser.parse(expression) for expression in options.cmd)
        collector = DockerCollector(client_factory(config, options.client, options.client_from_env), selector)
        self.engine = MonitoringEngine(collector, DockerActionExecutor(collector))

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
                            choices = RESOURCE_CHOICES,
                            help    = "resource information")

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not options.resource:
            options.resource = RESOURCE_CHOICES

    def _display_value(self, resource, value):
        return value if self.CMD_NAME == 'monit' else format_resource(resource, value)

    def _output_snapshot(self, snapshot):
        if self.options.output == 'json':
            values = dict((resource, self._display_value(resource, getattr(snapshot, resource)))
                          for resource in self.options.resource)
            sys.stdout.write(json.dumps({snapshot.name: values}) + '\n')
        else:
            values = [snapshot.name]
            for resource in self.options.resource:
                value = self._display_value(resource, getattr(snapshot, resource))
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
                                choices=RESOURCE_CHOICES, help='resource information')
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

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        super(MonitDockerSubCmdCron, cls).valid_subcmd_parser(parser, options)
        if not options.cmd:
            parser.error('cron requires --cmd or --cmd-if')
        if not options.state_file.strip():
            parser.error('--state-file must not be empty')
        if not math.isfinite(options.cooldown) or options.cooldown < 0:
            parser.error('--cooldown must be a finite non-negative number')

    def __call__(self):
        from monit_docker.adapters.state import LocalState
        with LocalState(self.options.state_file) as state:
            policy = CooldownPolicy(state, self.rules, self.options.cooldown)
            return self.engine.run_once(rules=self.rules, resources=(),
                                        dry_run=self.options.dry_run, action_policy=policy,
                                        on_action=self._output_action)


_SUBCMDS['cron'] = MonitDockerSubCmdCron
_SUBCMDS['monit'] = MonitDockerSubCmdMonit
_SUBCMDS['stats'] = MonitDockerSubCmdStats


def main(options):
    """
    Main function
    """
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
