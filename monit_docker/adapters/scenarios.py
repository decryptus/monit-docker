"""Resolve YAML scenarios into neutral validated jobs, without command parsing."""

import math
import re

from monit_docker.job import JobOptions, validate_job
from monit_docker.adapters.validation_rules import require, mapping, string_list, checked, _validate_rule, ConfigurationCheckError
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.filesystems import directory_groups
from monit_docker.domain.filesystems import filesystem_resource, FILESYSTEM_ACCESS_FIELDS

SCENARIO_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_SELECT_OPTIONS = {'name':'name', 'id':'id', 'image':'image', 'label':'label', 'group':'ctn_grp', 'status':'status'}
_GLOBAL_OPTIONS = frozenset(('client', 'client-from-env', 'audit-file', 'audit-max-bytes', 'audit-files', 'event-window'))
_VALUE_OPTIONS = frozenset(('state-file','cooldown','max-restarts','trigger-after','max-gap','interval','stale-after','bind','port','output'))
_LIST_OPTIONS = {'rules':'cmd', 'resources':'resource'}
_BOOLEAN_FIELDS = frozenset(('client-from-env', 'dry-run', 'all'))
_INTEGER_FIELDS = frozenset(('max-restarts', 'port', 'audit-max-bytes', 'audit-files', 'event-window'))
_NUMBER_FIELDS = frozenset(('cooldown', 'trigger-after', 'max-gap', 'interval', 'stale-after'))
_POLICY_FIELDS = frozenset(('state-file', 'cooldown', 'max-restarts', 'trigger-after', 'max-gap', 'dry-run'))
_COMMON_FIELDS = frozenset(('mode', 'description', 'select', 'all')) | frozenset(_GLOBAL_OPTIONS)
_MODE_FIELDS = {
    'stats': _COMMON_FIELDS | frozenset(('resources', 'output')),
    'cron': _COMMON_FIELDS | _POLICY_FIELDS | frozenset(('rules',)),
    'serve': _COMMON_FIELDS | _POLICY_FIELDS | frozenset((
        'rules', 'resources', 'bind', 'port', 'interval', 'stale-after')),
}
SCENARIO_FIELDS = frozenset().union(*_MODE_FIELDS.values())


def _strings(value, location):
    values = [value] if isinstance(value, str) else value
    string_list(values, location)
    return values


def scenario_options(name, entry):
    """Validate field types and construct an application request."""
    location = 'scenarios.%s' % name
    require(isinstance(name, str) and SCENARIO_NAME.fullmatch(name), 'scenarios', 'invalid scenario name')
    mapping(entry, location)
    entry = dict(entry)
    mode = entry.get('mode', 'cron')
    require(isinstance(mode, str) and mode in _MODE_FIELDS, location, 'mode must be stats, cron or serve')
    require(not set(entry) - _MODE_FIELDS[mode], location, 'unknown or unsupported field for this mode')
    for field, value in entry.items():
        where = location + '.' + field
        if field in _BOOLEAN_FIELDS:
            require(type(value) is bool, where, 'expected a boolean')
        elif field in _INTEGER_FIELDS or field in _NUMBER_FIELDS:
            # Mako substitutions rendered inside YAML strings remain strings.
            require(type(value) in (int, str) if field in _INTEGER_FIELDS else type(value) in (int, float, str),
                    where, 'expected an integer' if field in _INTEGER_FIELDS else 'expected a finite number')
            try:
                number = int(value) if field in _INTEGER_FIELDS else float(value)
                valid = math.isfinite(number)
            except (ValueError, OverflowError):
                valid = False
            require(valid, where, 'invalid numeric value')
            entry[field] = number
        elif field not in _LIST_OPTIONS and field != 'select':
            require(isinstance(value, str) and bool(value.strip()), where, 'expected a nonempty string')
    select = entry.get('select', {})
    mapping(select, location + '.select')
    require(not set(select) - set(_SELECT_OPTIONS), location + '.select', 'unknown selector field')
    require(bool(select) != bool(entry.get('all')), location, 'specify select or all: true, exclusively')
    require('group' not in select or not set(select) - {'group', 'status'}, location + '.select',
            'group cannot be combined with name, id, image or label selectors')
    values = {'subcommand': mode}
    for field, value in select.items():
        values[_SELECT_OPTIONS[field]] = tuple(_strings(value, location + '.select.' + field))
    for field in _GLOBAL_OPTIONS | _VALUE_OPTIONS | {'dry-run'}:
        if field in entry:
            values[field.replace('-', '_')] = entry[field]
    for field, attribute in _LIST_OPTIONS.items():
        if field in entry:
            values[attribute] = tuple(_strings(entry[field], location + '.' + field))
    try:
        return validate_job(JobOptions(**values))
    except (ValueError, TypeError):
        # Never echo rendered values or secrets in invalid-option diagnostics.
        raise ConfigurationCheckError(location, 'invalid scenario options for the selected mode') from None


def validate_scenario(config, name):
    """Resolve one scenario offline, including aliases and all rule conditions."""
    require(isinstance(name, str) and SCENARIO_NAME.fullmatch(name), 'scenarios', 'invalid scenario name')
    location = 'scenarios.' + name
    require(name in config.get('scenarios', {}), location, 'unknown scenario')
    entry = config['scenarios'][name]
    options = scenario_options(name, entry)
    require(not options.client or options.client_from_env or options.client in config.get('clients', {}),
            location + '.client', 'unknown client name')
    checked(location + '.select', lambda: ContainerSelector(
        selectors=dict((kind, getattr(options, kind)) for kind in ('id', 'name', 'image', 'label')),
        statuses=options.status, groups=config.get('ctn-groups'), selected_groups=options.ctn_grp))
    groups = config.get('dir-groups', {})
    checked(location + '.resources', lambda: directory_groups(groups))
    for resource in options.resource:
        filesystem = filesystem_resource(resource)
        require(not filesystem or filesystem[1] in groups, location + '.resources', 'unknown directory group')
        if filesystem and filesystem[0] in FILESYSTEM_ACCESS_FIELDS:
            require(groups[filesystem[1]].get('access') is not None, location + '.resources',
                    'access identity is required for filesystem access resources')
    parser = checked(location + '.rules', lambda: RuleParser(config.get('commands'), config.get('conditions'), groups))
    for index, expression in enumerate(getattr(options, 'cmd', ())):
        _validate_rule(parser, expression, '%s.rules[%s]' % (location, index))
    return options


