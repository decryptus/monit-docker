"""Named configurations compiled into the existing CLI, never a shell command."""

import argparse
import json
import math
import re
import sys

from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.filesystems import directory_groups
from monit_docker.domain.filesystems import filesystem_resource, FILESYSTEM_ACCESS_FIELDS

SCENARIO_NAME = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')
_SELECT_OPTIONS = {
    'name': '--name', 'id': '--id', 'image': '--image', 'label': '--label',
    'group': '--ctn-group', 'status': '-s',
}
_SELECT_ATTRIBUTES = ('name', 'id', 'image', 'label', 'ctn_grp', 'status')
_GLOBAL_OPTIONS = {
    'client': '--client', 'client-from-env': '--client-from-env',
    'audit-file': '--audit-file', 'audit-max-bytes': '--audit-max-bytes',
    'audit-files': '--audit-files',
    'event-window': '--event-window',
}
_VALUE_OPTIONS = {
    'state-file': '--state-file', 'cooldown': '--cooldown',
    'max-restarts': '--max-restarts', 'trigger-after': '--trigger-after',
    'max-gap': '--max-gap', 'interval': '--interval', 'stale-after': '--stale-after',
    'bind': '--bind', 'port': '--port', 'output': '--output',
}
_LIST_OPTIONS = {'rules': '--cmd-if', 'resources': '--rsc'}
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
_INHERITED_ATTRIBUTES = ('conffile', 'logfile', 'runtimedir', 'loglevel')
_INHERITED_GLOBALS = ('audit-file', 'audit-max-bytes', 'audit-files', 'event-window')


class _ScenarioParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse can include complete values (including secrets) in diagnostics.
        from monit_docker.adapters.validation import ConfigurationCheckError
        raise ConfigurationCheckError('options', 'invalid scenario options for the selected mode')


def reject_selection_overrides(parser, options):
    if any(getattr(options, field) for field in _SELECT_ATTRIBUTES) or options.client or options.client_from_env:
        parser.error('put container selection and Docker client settings in the scenario')


def _strings(value, location):
    from monit_docker.adapters.validation import string_list
    values = [value] if isinstance(value, str) else value
    string_list(values, location)
    return values


def scenario_arguments(name, entry):
    """Validate field types before passing values to the existing argument parser."""
    from monit_docker.adapters.validation import mapping, require
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
    arguments = []
    for field, value in select.items():
        arguments.extend('%s=%s' % (_SELECT_OPTIONS[field], item)
                         for item in _strings(value, location + '.select.' + field))
    for field, option in _GLOBAL_OPTIONS.items():
        if field not in entry:
            continue
        if field in _BOOLEAN_FIELDS:
            if entry[field]:
                arguments.append(option)
        else:
            arguments.append('%s=%s' % (option, entry[field]))
    arguments.append(mode)
    for field, option in _VALUE_OPTIONS.items():
        if field in entry:
            arguments.append('%s=%s' % (option, entry[field]))
    for field, option in _LIST_OPTIONS.items():
        if field in entry:
            arguments.extend('%s=%s' % (option, item)
                             for item in _strings(entry[field], location + '.' + field))
    if entry.get('dry-run'):
        arguments.append('--dry-run')
    return arguments


def validate_scenario(config, name):
    """Resolve one scenario offline, including aliases and all rule conditions."""
    from monit_docker import cli
    from monit_docker.adapters.validation import checked, require, _validate_rule, ConfigurationCheckError
    require(isinstance(name, str) and SCENARIO_NAME.fullmatch(name), 'scenarios', 'invalid scenario name')
    location = 'scenarios.' + name
    require(name in config.get('scenarios', {}), location, 'unknown scenario')
    entry = config['scenarios'][name]
    arguments = scenario_arguments(name, entry)
    try:
        options = cli.argv_parse_check(arguments, parser_class=_ScenarioParser)
    except ConfigurationCheckError as error:
        raise ConfigurationCheckError(location, str(error)) from error
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


def _load(options, inline):
    from monit_docker.adapters.validation import CheckedConfiguration
    return CheckedConfiguration(options.conffile, inline).load()


def _failure(error):
    print('Invalid scenario configuration: %s: %s' % (error.location, error), file=sys.stderr)
    return 110


def run_scenario(outer, inline=None):
    from monit_docker import cli
    from monit_docker.adapters.validation import ConfigurationCheckError, require
    try:
        config = _load(outer, inline)
        options = validate_scenario(config, outer.scenario)
        if outer.dry_run:
            require(bool(getattr(options, 'cmd', ())), 'scenarios.' + outer.scenario,
                    '--dry-run requires a scenario with rules')
            options.dry_run = True
        for field in _INHERITED_ATTRIBUTES:
            setattr(options, field, getattr(outer, field))
        for field in _INHERITED_GLOBALS:
            if field not in config['scenarios'][outer.scenario]:
                attribute = field.replace('-', '_')
                setattr(options, attribute, getattr(outer, attribute))
        # Execute exactly the configuration that was rendered and validated.
        options._scenario_config = config
    except ConfigurationCheckError as error:
        return _failure(error)
    return cli.main(options)


def inspect_scenarios(options, inline=None):
    from monit_docker.adapters.validation import ConfigurationCheckError
    try:
        config = _load(options, inline)
        if options.operation == 'show':
            validate_scenario(config, options.scenario)
            print(json.dumps(config['scenarios'][options.scenario], indent=2))
        else:
            entries = []
            for name, entry in sorted(config.get('scenarios', {}).items()):
                resolved = validate_scenario(config, name)
                entries.append(dict(name=name, mode=resolved.subcommand,
                                    description=entry.get('description', '')))
            print(json.dumps(entries, indent=2))
    except ConfigurationCheckError as error:
        return _failure(error)
    return 0
