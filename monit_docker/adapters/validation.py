"""Offline configuration checks using the runtime loader and rule grammar."""
import os
import re

import six

from monit_docker.adapters.configuration import Configuration, YamlLoader
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.filesystems import directory_groups
from monit_docker.adapters.validation_rules import (ConfigurationCheckError, require, mapping, string_list, checked, _validate_action_list, _validate_rule)
from monit_docker.adapters.scenarios import SCENARIO_FIELDS, SCENARIO_NAME, validate_scenario

_CONFIG_FIELDS = frozenset(('general', 'vars', 'clients', 'ctn-groups', 'dir-groups',
                            'conditions', 'commands', 'scenarios'))
_ENTRY_REQUIRED = {'client': 'config', 'ctn-group': 'match', 'dir-group': 'paths',
                   'condition': 'expr', 'command': 'exec', 'scenario': None}
_VALIDATED_SECTIONS = (('clients', 'client'), ('ctn-groups', 'ctn-group'),
                       ('dir-groups', 'dir-group'), ('conditions', 'condition'),
                       ('commands', 'command'), ('scenarios', 'scenario'))
_ALIAS_NAME = re.compile(r'[a-zA-Z][a-zA-Z0-9_.-]{0,64}')
_ENTRY_VARIABLES = frozenset(('vars', '@import_vars'))


class CheckedConfiguration(Configuration):
    """Shared strict loader for configuration inspection and execution."""
    def load(self, include_rules=True, allow_missing=False):
        self.source = (os.path.abspath(self.conffile) if os.path.exists(self.conffile)
                       else 'MONIT_DOCKER_CONFIG' if self.inline else os.path.abspath(self.conffile))
        self._root_pending = True
        if allow_missing and not os.path.exists(self.conffile) and not self.inline:
            return {}
        require(os.path.exists(self.conffile) or bool(self.inline), self.source,
                'configuration file not found and MONIT_DOCKER_CONFIG is not set')
        return checked(self.source, lambda: super(CheckedConfiguration, self).load(include_rules))

    def _load_yaml(self, stream, loader=YamlLoader):
        result = super(CheckedConfiguration, self)._load_yaml(stream, loader)
        if self._root_pending:
            self._root_pending = False
            mapping(result, self.source)
            require(not set(result) - _CONFIG_FIELDS, self.source, 'unknown top-level section')
            for name, value in result.items():
                mapping(value, '%s: %s' % (self.source, name))
        return result

    def _parse_import_file(self, conf, name, config_dir, xvars=None):
        location = '@import_%s' % name
        mapping(conf, location)
        if location in conf:
            value = conf[location]
            if isinstance(value, six.string_types):
                value = [value]
            string_list(value, location)
        result = super(CheckedConfiguration, self)._parse_import_file(conf, name, config_dir, xvars)
        if name != 'vars':
            self._entries(result, name)
        return result

    def _import_conf_file(self, filepath, config_dir=None, xvars=None):
        location = os.path.join(config_dir or '', filepath)
        def load():
            result = super(CheckedConfiguration, self)._import_conf_file(filepath, config_dir, xvars)
            mapping(result, location)
            return result
        return checked(location, load)

    @staticmethod
    def _entries(conf, kind):
        mapping(conf, kind)
        required = _ENTRY_REQUIRED[kind]
        for name, value in conf.items():
            location = '%s.%s' % (kind, name)
            if name.startswith('@'):
                require(name in ('@import_' + kind, '@import_vars'), location, 'unknown import directive')
                continue
            require(bool(name), kind, 'entry name must not be empty')
            if kind in ('condition', 'command', 'dir-group'):
                require(_ALIAS_NAME.fullmatch(name), location, 'invalid alias name')
            mapping(value, location)
            if kind == 'scenario':
                require(SCENARIO_NAME.fullmatch(name), location, 'invalid scenario name')
                allowed = SCENARIO_FIELDS | _ENTRY_VARIABLES
            else:
                require(required in value, location, 'missing %s' % required)
                allowed = {required} | _ENTRY_VARIABLES
                if kind == 'dir-group':
                    allowed = allowed | {'access'}
            require(not set(value) - allowed, location, 'unknown entry field')
            if 'vars' in value:
                mapping(value['vars'], location + '.vars')

    def _load_conf_section(self, xtype, section, conf, config_dir=None):
        def load():
            self._entries(conf, xtype)
            return super(CheckedConfiguration, self)._load_conf_section(xtype, section, conf, config_dir)
        return checked('%s: %s' % (self.source, xtype), load)


def check_configuration(conffile, inline=None, selectors=None, selected_groups=(), client=None,
                        from_env=False, expressions=()):
    loader = CheckedConfiguration(conffile, inline)
    config = loader.load()
    return validate_configuration(config, selectors, selected_groups, client, from_env, expressions)


def validate_configuration(config, selectors=None, selected_groups=(), client=None,
                           from_env=False, expressions=()):
    mapping(config, 'configuration')
    require(not set(config) - _CONFIG_FIELDS, 'configuration', 'unknown top-level section')
    for section, kind in _VALIDATED_SECTIONS:
        CheckedConfiguration._entries(config.get(section, {}), kind)
    for name, entry in config.get('clients', {}).items():
        mapping(entry['config'], 'clients.%s.config' % name)
        if 'tls' in entry['config']:
            tls = entry['config']['tls']
            require(isinstance(tls, (dict, bool)), 'clients.%s.config.tls' % name,
                    'expected a TLS mapping or boolean')
    if client:
        require(client in config.get('clients', {}), '--client', 'unknown client name')
    for name, group in config.get('ctn-groups', {}).items():
        location = 'ctn-groups.%s.match' % name
        string_list(group['match'], location)
        checked(location, lambda: ContainerSelector(groups={name: group}))
    checked('selectors', lambda: ContainerSelector(selectors=selectors, groups=config.get('ctn-groups'),
                                                   selected_groups=selected_groups))
    commands, conditions = config.get('commands', {}), config.get('conditions', {})
    dir_groups = config.get('dir-groups', {})
    checked('dir-groups', lambda: directory_groups(dir_groups))
    for name, entry in commands.items():
        location = 'commands.%s.exec' % name
        _validate_action_list(entry['exec'], location)
        parser = checked(location, lambda: RuleParser(commands={name: entry}))
        _validate_rule(parser, '@' + name, location)
    for name, entry in conditions.items():
        location = 'conditions.%s.expr' % name
        string_list(entry['expr'], location)
        parser = checked(location, lambda: RuleParser(conditions={name: entry}, dir_groups=dir_groups))
        _validate_rule(parser, '@%s ? reload' % name, location)
    parser = RuleParser(commands, conditions, dir_groups)
    for index, expression in enumerate(expressions):
        _validate_rule(parser, expression, '--cmd-if[%s]' % index)
    for name in config.get('scenarios', {}):
        checked('scenarios.' + name, lambda: validate_scenario(config, name))
    summary = {'clients': len(config.get('clients', {})), 'groups': len(config.get('ctn-groups', {})),
               'commands': len(commands), 'conditions': len(conditions), 'rules': len(expressions)}
    if config.get('scenarios'):
        summary['scenarios'] = len(config['scenarios'])
    return summary

