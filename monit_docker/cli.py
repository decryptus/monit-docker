#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright 2019-2022 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Legacy-compatible command-line interface for monit-docker."""

from __future__ import absolute_import

import argparse
import codecs
import copy
import fnmatch
import itertools
import json
import os
import re
import sys

from collections import OrderedDict

import logging
from logging.handlers import WatchedFileHandler

import six

try:
    from six.moves import cStringIO as StringIO
except ImportError:
    from six import StringIO

import docker
from docker.errors import APIError, DockerException

import bitmath

from mako.template import Template

from sonicprobe import helpers

import yaml

from monit_docker import __version__
from monit_docker.core import ResourceCalculator
from monit_docker.domain import ContainerSnapshot
from monit_docker.outputs.formatting import format_resource

try:
    from yaml import CSafeLoader as YamlLoader, CSafeDumper as YamlDumper
except ImportError:
    from yaml import SafeLoader as YamlLoader, SafeDumper as YamlDumper


SYSLOG_NAME             = "monit-docker"
LOG                     = logging.getLogger(SYSLOG_NAME)

DEFAULT_CONFFILE        = "/etc/monit-docker/monit-docker.yml"
DEFAULT_LOGFILE         = "/var/log/monit-docker/monit-docker.log"
DEFAULT_RUNTIMEDIR      = "/run/monit-docker"

MONIT_DOCKER_CONFIG     = os.environ.get('MONIT_DOCKER_CONFIG')
MONIT_DOCKER_CONFFILE   = os.environ.get('MONIT_DOCKER_CONFFILE') or DEFAULT_CONFFILE
MONIT_DOCKER_LOGFILE    = os.environ.get('MONIT_DOCKER_LOGFILE') or DEFAULT_LOGFILE
MONIT_DOCKER_RUNTIMEDIR = os.environ.get('MONIT_DOCKER_RUNTIMEDIR') or DEFAULT_RUNTIMEDIR

_SUBCMDS                = {}
_TPL_IMPORTS            = ('from os import environ as ENV',
                           'from sonicprobe.helpers import to_yaml as my')

DOCKER_COMMANDS         = ('start',
                           'stop',
                           'remove',
                           'reload',
                           'restart',
                           'kill',
                           'pause',
                           'unpause')

RESOURCE_CHOICES        = ('mem_usage',
                           'mem_limit',
                           'mem_percent',
                           'cpu_percent',
                           'io_read',
                           'io_write',
                           'net_tx',
                           'net_rx',
                           'status',
                           'pid')

STATUS_RC               = {'running': 0,
                           'created': 10,
                           'paused': 20,
                           'restarting': 30,
                           'removing': 40,
                           'exited': 50,
                           'dead': 60}

DATATYPES               = RESOURCE_CHOICES
DATATYPES_BEFORE_RUN    = ('pid',
                           'status',)

PRE_COND_RE             = (r'(?:\s*(?P<pre_value>[0-9]+(?:\.[0-9]+)?\s*(?P<pre_value_unit>[a-zA-Z]+)?)\s+' +
                           r'(?P<pre_op>[\!\<\>=]=|[\<\>])\s+)?\s*')
DATATYPE_RE             = r'(?P<datatype>[a-z_]+)\s*'
OP_RE                   = r'(?P<op>[\!\<\>=]=|[\<\>]|\s+in\s+|\s+not in\s+)\s*'
VALUE_RE                = r'(?P<value>(?:[0-9]+(?:\.[0-9]+)?\s*(?P<value_unit>[a-zA-Z]+)?|[a-z]+|\((?:[a-z]+\,?){1,64}\)))'
CMD_RE                  = r'(?P<cmd>[^@].{2,})'
CMD_ALIAS_RE            = r'@(?P<cmd_alias>[a-zA-Z][a-zA-Z0-9_\-\.]{0,64})'
COND_ALIAS_RE           = r'@(?P<cond_alias>[a-zA-Z][a-zA-Z0-9_\-\.]{0,64})'

COND_MATCH              = re.compile(r'^' + PRE_COND_RE + DATATYPE_RE + OP_RE + VALUE_RE + r'\s*$').match

CMD_MATCH               = re.compile(r'^\s*' + CMD_RE + r'\s*$').match
CTN_GRP_MATCH           = re.compile(r'^(?P<subset>id|image|name|label)\s*:\s*(?P<pattern>.+)\s*$').match

EXPR_MATCH              = re.compile(r'^(?:(?:(?P<cond>' + PRE_COND_RE + DATATYPE_RE + OP_RE + VALUE_RE + r')\s*|' +
                                     r'\s*' + COND_ALIAS_RE + r')\s*' +
                                     r'\?)?\s*(?:' + CMD_ALIAS_RE + r'|' + CMD_RE + r')\s*$').match


StatusLoopContinue      = object()
StatusLoopRunContinue   = object()
StatusLoopBreak         = object()
StatusFuncReturn        = object()

try:
    codecs.lookup_error('surrogateescape')
    HAS_SURROGATEESCAPE = True
except LookupError:
    HAS_SURROGATEESCAPE = False

_COMPOSED_ERROR_HANDLERS = frozenset((None, 'surrogate_or_replace',
                                      'surrogate_or_strict',
                                      'surrogate_then_replace'))


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


class MonitDockerDataTypeError(TypeError):
    pass


class MonitDockerExprParserError(SyntaxError):
    pass


class MonitDockerSubCmdAbstract(object): #pylint: disable=useless-object-inheritance
    def __init__(self, options):
        self.options      = options
        self.config       = {}
        self.client       = None
        self._common_conf = {}
        self._config_dir  = ""
        self._containers  = {}
        self._subsets     = {}
        self._CTN_GRPS    = {}
        self._calculator  = ResourceCalculator()

        self._reset_subsets()
        self.load_conf()
        self.load_client()
        self.has_subsets  = self._load_subsets()

    @classmethod
    def load_subcmd_parser(cls, subparsers): #pylint: disable=unused-argument
        return

    @classmethod
    def valid_subcmd_parser(cls, parser, options): #pylint: disable=unused-argument
        return

    @staticmethod
    def _subset_pattern(pattern_str):
        if not pattern_str.startswith('~'):
            return re.compile(fnmatch.translate(pattern_str)).match

        return re.compile(pattern_str[1:]).match

    @staticmethod
    def _load_yaml(stream, loader = YamlLoader):
        return yaml.load(stream, Loader = loader)

    @staticmethod
    def _dump_yaml(stream, dumper = YamlDumper, default_flow_style = True):
        return yaml.dump(stream, Dumper = dumper, default_flow_style = default_flow_style)

    @staticmethod
    def _load_clients_conf_finalize(name, conf): #pylint: disable=unused-argument
        if conf.get('tls') \
           and isinstance(conf['tls'], dict):
            conf['tls'] = docker.tls.TLSConfig(**conf['tls'])

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

    def _reset_subsets(self):
        self._subsets = {'id': [],
                         'image': [],
                         'name': [],
                         'label': []}

    def _load_ctn_grp_conf_finalize(self, name, matches):
        r = {}

        for match in matches:
            m = CTN_GRP_MATCH(match)
            if not m:
                raise MonitDockerExprParserError("unable to parse container group match: %r" % match)

            subset  = m.group('subset')
            pattern = m.group('pattern')

            if subset not in r:
                r[subset] = []

            if subset == 'id' \
               and len(pattern) == 12 \
               and pattern.isalnum():
                pattern += '*'

            r[subset].append(self._subset_pattern(pattern))

        self._CTN_GRPS[name] = r

    def load_client(self):
        if self.options.client_from_env \
           or not self.config \
           or not self.config.get('clients'):
            if not os.environ.get('DOCKER_HOST'):
                os.environ['DOCKER_HOST'] = "unix:///var/run/docker.sock"
            self.client = docker.from_env()
            return

        if self.options.client:
            if self.options.client in self.config['clients']:
                client = self.config['clients'][self.options.client]
            else:
                LOG.error("unknown client: %r", self.options.client)
                raise MonitDockerExit(110)
        else:
            client = self.config['clients'][list(self.config['clients'])[0]]

        self.client = docker.DockerClient(**client['config'])

    def _import_conf_file(self, filepath, config_dir = None, xvars = None):
        if not xvars:
            xvars = {}

        if config_dir and not filepath.startswith(os.path.sep):
            filepath = os.path.join(config_dir, filepath)

        with open(filepath, 'r') as f:
            return self._load_yaml(
                Template(f.read(),
                         imports = _TPL_IMPORTS).render(**xvars))

    def _parse_import_file(self, conf, name, config_dir, xvars = None):
        r = OrderedDict()

        import_key = "@import_%s" % name

        if not conf.get(import_key):
            return r

        if isinstance(conf[import_key], six.string_types):
            c = [conf[import_key]]
        else:
            c = conf[import_key]

        for import_file in c:
            r.update(
                self._import_conf_file(
                    import_file,
                    config_dir,
                    xvars))

        return r

    def _render_conf_object(self, conf, xvars = None):
        if not xvars:
            xvars = {}

        return self._load_yaml(
            Template(self._dump_yaml(conf, default_flow_style = False),
                     imports = _TPL_IMPORTS).render(**xvars))

    def _load_conf_section(self, xtype, section, conf, finalizer = None, config_dir = None):
        r = OrderedDict()

        if not config_dir:
            config_dir = self._config_dir

        xvars = copy.deepcopy(self._common_conf)
        xvars.update(self._parse_import_file(conf, 'vars', config_dir, xvars))

        r     = self._parse_import_file(conf, xtype, config_dir, xvars)
        r.update(conf)

        for name, value in six.iteritems(copy.copy(r)):
            if name.startswith('@'):
                del r[name]
                continue

            c = copy.deepcopy(self._common_conf)
            c.update(copy.deepcopy(xvars))
            c["%s_name" % xtype] = name
            c['vars'].update(copy.deepcopy(xvars['vars']))
            c['vars'].update(self._parse_import_file(value, 'vars', config_dir, c))

            if 'vars' in value:
                c['vars'].update(copy.deepcopy(value['vars']))

            if name not in r:
                r[name] = {section: {}}

            if section in value:
                r[name][section] = self._render_conf_object(value[section], c)
            else:
                LOG.error("missing %s in %s: %r", section, xtype, name)
                raise MonitDockerExit(110)

            if finalizer:
                finalizer(name, r[name][section])

            for x in ('vars', '@import_vars'):
                if x in r[name]:
                    del r[name][x]

        return r

    def load_conf(self):
        if os.path.exists(self.options.conffile):
            self._config_dir = os.path.dirname(os.path.abspath(self.options.conffile))

            with open(self.options.conffile, 'r') as f:
                conf = self._load_yaml(f)
        elif MONIT_DOCKER_CONFIG:
            c = StringIO(MONIT_DOCKER_CONFIG)
            conf = self._load_yaml(c.getvalue())
            c.close()
        else:
            return {}

        self._common_conf = {'general': {},
                             'vars': {}}

        if conf.get('general'):
            self._common_conf['general'] = dict(conf['general'])

        if conf.get('vars'):
            self._common_conf['vars'] = dict(conf['vars'])

        if conf.get('clients'):
            self.config['clients'] = self._load_conf_section('client',
                                                             'config',
                                                             conf['clients'],
                                                             finalizer = self._load_clients_conf_finalize)

        if conf.get('ctn-groups'):
            self.config['ctn-groups'] = self._load_conf_section('ctn-group',
                                                                'match',
                                                                conf['ctn-groups'],
                                                                finalizer = self._load_ctn_grp_conf_finalize)

        return conf

    def _load_subsets(self):
        r = False
        for subset_type, subset_patterns in six.iteritems(self._subsets):
            subset_opt = getattr(self.options, subset_type)
            if not subset_opt:
                continue

            r = True

            for search_pattern in self._split_search_pattern(subset_opt):
                if subset_type == 'id' \
                   and len(search_pattern) == 12 \
                   and search_pattern.isalnum():
                    search_pattern += '*'
                subset_patterns.append(self._subset_pattern(search_pattern))

        return r

    def _match_containers(self, xall = False):
        r = OrderedDict()

        containers = self.client.containers.list(**{'all': xall})

        if not containers:
            LOG.warning("no container found")
            return r

        for container in containers:
            if self.options.status \
               and container.status not in self.options.status:
                continue

            if not self.has_subsets:
                r[container.id] = container
                continue

            matched = False
            for subset_type, patterns in six.iteritems(self._subsets):
                if not patterns:
                    continue

                for pattern in patterns:
                    if subset_type in ('id', 'name'):
                        if pattern(getattr(container, subset_type)):
                            matched = True
                            r[container.id] = container
                            break
                    elif subset_type == 'label':
                        for label in six.itervalues(container.labels):
                            if pattern(label):
                                matched = True
                                r[container.id] = container
                                break
                    elif subset_type == 'image':
                        for tag in container.image.tags:
                            if pattern(tag):
                                matched = True
                                r[container.id] = container
                                break
                    if matched:
                        break
                if matched:
                    break

        return r

    def to_text(self, obj, encoding='utf-8', errors=None, nonstring='simplerepr'):
        """ function from project ansible: ansible/module_utils/_text.py """

        if isinstance(obj, six.text_type):
            return obj

        if errors in _COMPOSED_ERROR_HANDLERS:
            if HAS_SURROGATEESCAPE:
                errors = 'surrogateescape'
            elif errors == 'surrogate_or_strict':
                errors = 'strict'
            else:
                errors = 'replace'

        if isinstance(obj, six.binary_type):
            # Note: We don't need special handling for surrogate_then_replace
            # because all bytes will either be made into surrogates or are valid
            # to decode.
            return obj.decode(encoding, errors)

        # Note: We do these last even though we have to call to_text again on the
        # value because we're optimizing the common case
        if nonstring == 'simplerepr':
            try:
                value = str(obj)
            except UnicodeError:
                try:
                    value = repr(obj)
                except UnicodeError:
                    # Giving up
                    return u''
        elif nonstring == 'passthru':
            return obj
        elif nonstring == 'empty':
            return u''
        elif nonstring == 'strict':
            raise TypeError('obj must be a string type')
        else:
            raise TypeError('Invalid value %s for to_text\'s nonstring parameter' % nonstring)

        return self.to_text(value, encoding, errors)

    def _split_search_pattern(self, pattern):
        if isinstance(pattern, list):
            return list(itertools.chain(*map(self._split_search_pattern, pattern)))
        if not isinstance(pattern, six.string_types):
            pattern = self.to_text(pattern, errors='surrogate_or_strict')

        if u',' in pattern:
            patterns = pattern.split(u',')
        else:
            patterns = [pattern]

        return [p.strip() for p in patterns]

    def terminate(self):
        if self.client:
            self.client.api.close()


class MonitDockerSubCmdStats(MonitDockerSubCmdAbstract):
    CMD_NAME = 'stats'
    CMD_HELP = "display stats information"

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

    def _get_resource_info(self, rsc, current, previous):
        if rsc not in RESOURCE_CHOICES or rsc in ('pid', 'status'):
            LOG.error("resource unknown: %r", rsc)
            raise MonitDockerExit(112)
        return self._calculator.get(rsc, current, previous)

    def before_run(self, container): #pylint: disable=no-self-use
        if container['obj'].status not in ('paused', 'running'):
            return StatusLoopRunContinue

        return None

    def _snapshot(self, container, data):
        values = {'id': container['id'], 'name': container['name'],
                  'status': container['obj'].status,
                  'pid': container['obj'].attrs['State'].get('Pid')}
        for rsc in self.options.resource:
            if rsc not in ('pid', 'status'):
                values[rsc] = (self._get_resource_info(rsc, data, container['stats'])
                               if data else None)
        return ContainerSnapshot(**values)

    def _display_value(self, resource, value):
        return value if self.CMD_NAME == 'monit' else format_resource(resource, value)

    def _output_text(self, container, data):
        snapshot = self._snapshot(container, data)
        r = ["%s" % snapshot.name]

        for rsc in self.options.resource:
            if rsc == 'pid':
                r.append("%s:%s" % (rsc, snapshot.pid or 'null'))
            elif rsc == 'status':
                r.append("%s:%s" % (rsc, snapshot.status))
            elif data:
                r.append("%s:%s" % (rsc, self._display_value(rsc, getattr(snapshot, rsc))))
            else:
                r.append("%s:%s" % (rsc, 'null'))

        sys.stdout.write('|'.join(r) + "\n")

    def _output_json(self, container, data):
        snapshot = self._snapshot(container, data)
        r = {snapshot.name: {}}

        for rsc in self.options.resource:
            if rsc == 'pid':
                r[snapshot.name][rsc] = snapshot.pid
            elif rsc == 'status':
                r[snapshot.name][rsc] = snapshot.status
            elif data:
                r[snapshot.name][rsc] = self._display_value(rsc, getattr(snapshot, rsc))
            else:
                r[snapshot.name][rsc] = None

        sys.stdout.write(json.dumps(r) + "\n")

    def run(self, container, data):
        getattr(self, "_output_%s" % getattr(self.options, 'output', 'json'))(container, data)

        return StatusLoopBreak

    def __call__(self):
        if self.options.ctn_grp:
            for name in self.options.ctn_grp:
                if name not in self._CTN_GRPS:
                    LOG.error("unable to find container group: %r", name)
                    raise MonitDockerExit(110)

            self._reset_subsets()

            for name in self.options.ctn_grp:
                for subset_type, patterns in six.iteritems(self._CTN_GRPS[name]):
                    self.has_subsets = True
                    self._subsets[subset_type].extend(patterns)

        containers = self._match_containers(xall = True)
        if not containers:
            LOG.warning("no container found")
            raise MonitDockerExit(114)

        for xid, obj in six.iteritems(containers):
            if xid not in self._containers:
                self._containers[xid] = {'obj': obj,
                                         'id': xid,
                                         'name': obj.name,
                                         'stats': None}

            container = self._containers[xid]

            r = self.before_run(container)
            if r in (StatusLoopContinue, StatusLoopRunContinue):
                if r is StatusLoopRunContinue:
                    self.run(container, {})
                continue

            for line in container['obj'].stats(stream = True):
                data = json.loads(line)

                if not container['stats']:
                    container['stats'] = data
                    continue
                if data['read'] == container['stats']['read']:
                    continue

                r = self.run(container, data)
                if r is StatusLoopBreak:
                    break


class MonitDockerSubCmdMonit(MonitDockerSubCmdStats):
    CMD_NAME    = 'monit'
    CMD_HELP    = "return stats information with return code"
    def __init__(self, options):
        self._COMMANDS = {}
        self._CONDITIONS = {}
        self._EXPRS = {}
        super(MonitDockerSubCmdMonit, self).__init__(options)

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME,
                                       help = cls.CMD_HELP)
        parser.add_argument("--rsc",
                            action  = 'append',
                            dest    = 'resource',
                            default = [],
                            choices = RESOURCE_CHOICES,
                            help    = "resource information")
        parser.add_argument("--cmd",
                            "--cmd-if",
                            action  = 'append',
                            dest    = 'cmd',
                            default = [],
                            help    = "run docker command or execute command inside containers")

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if options.resource and options.cmd:
            parser.error("rsc and cmd options can't be in the same command")

        setattr(options, 'output', 'text')

    def _load_conditions_conf_finalize(self, name, exprs):
        r = []

        for expr in exprs:
            m = COND_MATCH(expr)
            if not m:
                raise MonitDockerExprParserError("unable to parse conditional expression: %r" % expr)

            m = m.groupdict()

            if m.get('pre_value_unit'):
                m['pre_value'] = bitmath.parse_string(m['pre_value']).bytes

            if m.get('value_unit'):
                m['value'] = bitmath.parse_string(m['value']).bytes

            r.append({'real': expr,
                      'parsed': m})

        self._CONDITIONS[name] = r

    def _load_commands_conf_finalize(self, name, cmds):
        r = []

        for cmd in cmds:
            xdict = {'args': [],
                     'kwargs': {}}
            if not isinstance(cmd, dict):
                c = cmd
            else:
                c = list(cmd)[0]
                if 'args' in cmd[c]:
                    xdict['args']   = cmd[c]['args']
                if 'kwargs' in cmd[c]:
                    xdict['kwargs'] = cmd[c]['kwargs']

            m = CMD_MATCH(c)
            if not m:
                raise MonitDockerExprParserError("unable to parse command expression: %r" % cmd)
            xdict['cmd'] = m.group('cmd')
            r.append(xdict)

        self._COMMANDS[name] = r

    def load_conf(self):
        conf = super(MonitDockerSubCmdMonit, self).load_conf()

        if conf.get('conditions'):
            self.config['conditions'] = self._load_conf_section('condition',
                                                                'expr',
                                                                conf['conditions'],
                                                                finalizer = self._load_conditions_conf_finalize)

        if conf.get('commands'):
            self.config['commands'] = self._load_conf_section('command',
                                                              'exec',
                                                              conf['commands'],
                                                              finalizer = self._load_commands_conf_finalize)

    def _parse_exprs(self, expr, datatypes):
        r = {'conditions': [],
             'commands': []}

        if expr not in self._EXPRS:
            m = EXPR_MATCH(expr)
            if not m:
                raise MonitDockerExprParserError("unable to parse expression: %r" % expr)

            m = m.groupdict()

            if m.get('pre_value_unit'):
                m['pre_value'] = bitmath.parse_string(m['pre_value']).bytes

            if m.get('value_unit'):
                m['value'] = bitmath.parse_string(m['value']).bytes

            if m['cond_alias']:
                cond_alias = m['cond_alias']
                if cond_alias not in self._CONDITIONS:
                    LOG.error("unknown conditional expression alias: %r", cond_alias)
                    raise MonitDockerExit(110)

                r['conditions'] = self._CONDITIONS[cond_alias]
            elif m['cond']:
                cond = dict(m)
                del cond['cmd']
                r['conditions'] = [{'real': cond['cond'],
                                    'parsed': cond}]

            if m['cmd_alias']:
                cmd_alias = m['cmd_alias']
                if cmd_alias not in self._COMMANDS:
                    LOG.error("unknown command alias: %r", cmd_alias)
                    raise MonitDockerExit(110)

                r['commands'] = self._COMMANDS[cmd_alias]
            else:
                r['commands'] = [{'cmd':    m['cmd'],
                                  'args':   [],
                                  'kwargs': {}}]

            self._EXPRS[expr] = r
        else:
            r = self._EXPRS[expr]

        for cond in r['conditions']:
            if cond['parsed']['datatype'] not in datatypes:
                raise MonitDockerDataTypeError("invalid specified datatype: %r" % cond['parsed']['datatype'])

        return r

    @staticmethod
    def _run_exec(cmd, container, args = None, kwargs = None):
        if not args:
            args = []

        if not kwargs:
            kwargs = {}

        LOG.info("execute %r in %s", cmd, container['name'])
        try:
            r = container['obj'].exec_run(cmd, *args, **kwargs)
        except APIError as e:
            LOG.error("unable to execute %r in %s: %r", cmd, container['name'], e)
            return False
        LOG.info("%r executed in %s: %r", cmd, container['name'], r)

        return r.exit_code == 0

    @staticmethod
    def _run_docker_cmd(cmd, container, args = None, kwargs = None):
        if not args:
            args = []

        if not kwargs:
            kwargs = {}

        try:
            LOG.info("execute %s on %s", cmd, container['name'])
            getattr(container['obj'], cmd)(*args, **kwargs)
            LOG.info("%s executed on %s", cmd, container['name'])
            return True
        except APIError as e:
            LOG.error("unable to execute %s on %s. (error: %r)", cmd, container['name'], e)

        return False

    @staticmethod
    def _condition_result(op, ret, val, enable_in = False):
        if op == '==':
            rs = ret == val
        elif op == '!=':
            rs = ret != val
        elif op == '>=':
            rs = ret >= val
        elif op == '<=':
            rs = ret <= val
        elif op == '>':
            rs = ret > val
        elif op == '<':
            rs = ret < val
        elif op == 'in' and enable_in:
            rs = ret in val
        elif op == 'not in' and enable_in:
            rs = ret not in val
        else:
            raise MonitDockerExprParserError("conditional operator unknown: %r" % op)

        return rs

    def _get_datatype_info(self, datatype, container, data):
        if datatype == 'pid':
            return container['obj'].attrs['State'].get('Pid') or ''

        if datatype == 'status':
            return container['obj'].status

        return self._get_resource_info(datatype, data, container['stats'])

    def _eval_if(self, condition, container, data = None):
        real_cond = condition['real']
        expr      = condition['parsed']

        datatype  = expr['datatype']
        op        = expr['op'].strip()
        ret       = self._get_datatype_info(datatype, container, data)

        pre_op    = expr['pre_op']
        pre_val   = expr['pre_value']

        if op in ('in', 'not in'):
            val = expr['value']
            if val.startswith('(') and val.endswith(')'):
                val = val[1:-1].split(',')
            else:
                raise MonitDockerExprParserError("invalid value with %r for expression if: %r" % (op, real_cond))
        else:
            try:
                val = type(ret)(expr['value'])
            except TypeError:
                LOG.error("invalid value for expression if: %r", real_cond)
                raise MonitDockerExit(110)

            if pre_val is not None:
                try:
                    pre_val = type(ret)(pre_val)
                except TypeError:
                    LOG.error("invalid pre value for expression if: %r", real_cond)
                    raise MonitDockerExit(110)

        if None not in (pre_op, pre_val):
            return self._condition_result(pre_op, ret, pre_val) \
               and self._condition_result(op, ret, val)

        return self._condition_result(op, ret, val, enable_in = True)

    def _parse_cmd(self, condition, command):
        cmd = command['cmd'].strip()

        if cmd.startswith('(') and cmd.endswith(')'):
            cmd = cmd[1:-1]
            if not cmd.strip():
                LOG.error("missing command to execute in expression: %r", condition)
                raise MonitDockerExit(110)
            return {'func':   self._run_exec,
                    'cmd':    cmd,
                    'args':   command['args'],
                    'kwargs': command['kwargs']}
        if cmd in DOCKER_COMMANDS:
            return {'func':   self._run_docker_cmd,
                    'cmd':    cmd,
                    'args':   command['args'],
                    'kwargs': command['kwargs']}

        LOG.error("invalid docker command: %r. (condition: %r)", cmd, condition)
        raise MonitDockerExit(110)

    def _run_cmd(self, expr, container, datatypes, data = None):
        exprs = self._parse_exprs(expr, datatypes)
        cmds  = []
        rs    = True
        r     = []

        for command in exprs['commands']:
            cmds.append(self._parse_cmd(expr, command))

        for cond in exprs['conditions']:
            if not self._eval_if(cond, container, data):
                rs = False
                break

        LOG.debug("commands: %r, conditions: %r, result: %r",
                  exprs['commands'],
                  exprs['conditions'],
                  rs)

        if not rs:
            return False

        for cmd in cmds:
            r.append(cmd['func'](cmd       = cmd['cmd'],
                                 container = container,
                                 args      = cmd['args'],
                                 kwargs    = cmd['kwargs']))
            if not r[-1]:
                LOG.error("command failed on %s: %r", container['name'], cmd['cmd'])
                raise MonitDockerExit(116)

        return r

    def before_run(self, container):
        if self.options.resource:
            if len(self.options.resource) == 1:
                if self.options.resource[0] == 'pid':
                    self._write_pidfile(container['obj'].attrs['State'].get('Pid') or '',
                                        container['name'])
                    return StatusLoopRunContinue
                if self.options.resource[0] == 'status':
                    raise MonitDockerExit(int(self._get_status_rc(container['obj'].status)))

            if container['obj'].status not in ('paused', 'running'):
                return StatusLoopRunContinue

            return None

        cmds = list(self.options.cmd)
        if cmds:
            to_pop = []
            for i, expr in enumerate(cmds):
                try:
                    self._run_cmd(expr, container, DATATYPES_BEFORE_RUN)
                except MonitDockerExprParserError:
                    raise
                except MonitDockerDataTypeError:
                    continue
                to_pop.append(i)

            for i in reversed(to_pop):
                cmds.pop(i)

            container['pending_cmds'] = cmds

            if not cmds:
                return StatusLoopContinue

        if container['obj'].status not in ('paused', 'running'):
            return StatusLoopContinue

        return None

    def run(self, container, data):
        if self.options.resource:
            if len(self.options.resource) > 1 or not self.options.resource[0].endswith('_percent'):
                return super(MonitDockerSubCmdMonit, self).run(container, data)

            rsc = self.options.resource[0]

            if data:
                value = self._get_resource_info(rsc, data, container['stats'])
                # Exit codes 101+ are reserved for errors. Docker CPU usage can
                # exceed 100% on multi-core hosts; keep raw values in stats/rules.
                raise MonitDockerExit(max(0, min(100, int(value))))

            LOG.error("no statistic for the container: %s", container['name'])
            raise MonitDockerExit(115)

        if self.options.cmd:
            for expr in container.get('pending_cmds', self.options.cmd):
                self._run_cmd(expr, container, DATATYPES, data)

            return StatusLoopBreak

        return super(MonitDockerSubCmdMonit, self).run(container, data)


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
    monit_docker = None

    try:
        monit_docker = _SUBCMDS[options.subcommand](options)
        monit_docker()
    except APIError as e:
        rc = 180
        LOG.error(e.explanation)
    except DockerException as e:
        rc = 170
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
    finally:
        if monit_docker:
            monit_docker.terminate()

    return rc


if __name__ == '__main__':
    sys.exit(main(argv_parse_check()))
