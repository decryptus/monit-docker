"""Normalize legacy expressions and unit strings into engine rule values."""

import copy
import logging
import bitmath
from monit_docker.adapters.syntax import (COND_MATCH, CMD_MATCH, EXPR_MATCH,
                                          DOCKER_COMMANDS, DATATYPES)
from monit_docker.domain.errors import (MonitoringError, RuleSyntaxError,
                                        ResourceTypeError)
from monit_docker.domain.rules import Action, Condition, Rule
from monit_docker.domain.filesystems import filesystem_resource, FILESYSTEM_MODES
from monit_docker.adapters.filesystems import directory_groups
from monit_docker.domain.models import HEALTH_STATES

LOG = logging.getLogger('monit-docker')
_STATE_OPERATORS = ('==', '!=', 'in', 'not in')


class RuleParser(object):
    def __init__(self, commands=None, conditions=None, dir_groups=None):
        self.dir_groups = directory_groups(dir_groups)
        self._COMMANDS = {}
        self._CONDITIONS = {}
        self._EXPRS = {}
        for name, value in (commands or {}).items():
            self._load_commands_conf_finalize(name, value['exec'])
        for name, value in (conditions or {}).items():
            self._load_conditions_conf_finalize(name, value['expr'])

    def _load_conditions_conf_finalize(self, name, exprs):
        r = []

        for expr in exprs:
            m = COND_MATCH(expr)
            if not m:
                raise RuleSyntaxError("unable to parse conditional expression: %r" % expr)

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
                raise RuleSyntaxError("unable to parse command expression: %r" % cmd)
            xdict['cmd'] = m.group('cmd')
            r.append(xdict)

        self._COMMANDS[name] = r

    def _parse_exprs(self, expr, datatypes):
        r = {'conditions': [],
             'commands': []}

        if expr not in self._EXPRS:
            m = EXPR_MATCH(expr)
            if not m:
                raise RuleSyntaxError("unable to parse expression: %r" % expr)

            m = m.groupdict()

            if m.get('pre_value_unit'):
                m['pre_value'] = bitmath.parse_string(m['pre_value']).bytes

            if m.get('value_unit'):
                m['value'] = bitmath.parse_string(m['value']).bytes

            if m['cond_alias']:
                cond_alias = m['cond_alias']
                if cond_alias not in self._CONDITIONS:
                    LOG.error("unknown conditional expression alias: %r", cond_alias)
                    raise MonitoringError(110, 'unknown alias in expression: %r' % expr)

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
                    raise MonitoringError(110, 'unknown alias in expression: %r' % expr)

                r['commands'] = self._COMMANDS[cmd_alias]
            else:
                r['commands'] = [{'cmd':    m['cmd'],
                                  'args':   [],
                                  'kwargs': {}}]

            self._EXPRS[expr] = r
        else:
            r = self._EXPRS[expr]

        for cond in r['conditions']:
            resource = cond['parsed']['datatype']
            filesystem = filesystem_resource(resource)
            if resource == 'health' or (filesystem and filesystem[0] == 'fs_mode'):
                choices = HEALTH_STATES if resource == 'health' else FILESYSTEM_MODES
                parsed = cond['parsed']
                operator = parsed['op'].strip()
                value = parsed['value']
                if operator in ('in', 'not in'):
                    valid = isinstance(value, str) and value.startswith('(') and value.endswith(')')
                    states = value[1:-1].split(',') if valid else ()
                else:
                    states = (value,)
                if (operator not in _STATE_OPERATORS or parsed.get('pre_value') is not None
                        or not states or any(state not in choices for state in states)):
                    raise RuleSyntaxError('%s requires a known state and an equality or membership comparison' % resource)
            if filesystem:
                if filesystem[1] not in self.dir_groups:
                    raise MonitoringError(110, 'unknown directory group: %s' % filesystem[1])
                if filesystem[0] == 'fs_mode':
                    continue
                if cond['parsed']['op'].strip() in ('in', 'not in'):
                    raise RuleSyntaxError('filesystem resources require numeric comparisons')
                for key in ('value', 'pre_value'):
                    value = cond['parsed'].get(key)
                    if value is not None:
                        try:
                            float(value)
                        except (TypeError, ValueError):
                            raise RuleSyntaxError('filesystem resources require numeric values')
            elif resource not in datatypes:
                raise ResourceTypeError("invalid specified datatype: %r" % cond['parsed']['datatype'])

        return r

    def parse(self, expression):
        parsed = self._parse_exprs(expression, DATATYPES)
        conditions = []
        for item in parsed['conditions']:
            condition = item['parsed']
            conditions.append(Condition(condition['datatype'], condition['op'],
                                        condition['value'], condition['pre_op'],
                                        condition['pre_value'], item['real']))
        actions = []
        for item in parsed['commands']:
            command = item['cmd'].strip()
            if command.startswith('(') and command.endswith(')'):
                kind, command = 'exec', command[1:-1]
                if not command.strip():
                    raise MonitoringError(110, 'missing command to execute: %r' % expression)
            elif command in DOCKER_COMMANDS:
                kind = 'docker'
            else:
                raise MonitoringError(110, 'invalid docker command: %r' % command)
            actions.append(Action(kind, command, tuple(copy.deepcopy(item['args'] or ())),
                                  copy.deepcopy(item['kwargs'] or {})))
        return Rule(expression, tuple(conditions), tuple(actions))
