"""Offline configuration checks using the runtime loader and rule grammar."""
import os
import re

import six
import yaml

from monit_docker.adapters.configuration import Configuration, YamlLoader
from monit_docker.adapters.rules import RuleParser
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError, ResourceTypeError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot, STATE_RESOURCES
from monit_docker.domain.runtime import EVENT_RESOURCES
from monit_docker.domain.rules import Rule
from monit_docker.adapters.filesystems import directory_groups
from monit_docker.domain.filesystems import FilesystemSample, FILESYSTEM_NUMERIC_FIELDS


class ConfigurationCheckError(ValueError):
    def __init__(self, location, message):
        super(ConfigurationCheckError, self).__init__(message)
        self.location = location


def require(condition, location, message):
    if not condition:
        raise ConfigurationCheckError(location, message)


def mapping(value, location):
    require(isinstance(value, dict), location, 'expected a mapping')
    require(all(isinstance(k, six.string_types) for k in value), location,
            'mapping keys must be strings')


def string_list(value, location):
    require(isinstance(value, list) and bool(value), location, 'expected a nonempty list of strings')
    require(all(isinstance(v, six.string_types) and v.strip() for v in value), location,
            'expected a nonempty list of strings')


def checked(location, callback):
    try:
        return callback()
    except ConfigurationCheckError:
        raise
    except yaml.YAMLError as error:
        mark = getattr(error, 'problem_mark', None)
        message = 'invalid YAML'
        if mark:
            message += ' at line %s, column %s' % (mark.line + 1, mark.column + 1)
        raise ConfigurationCheckError(location, message)
    except re.error:
        raise ConfigurationCheckError(location, 'invalid selector regular expression')
    except ResourceTypeError:
        raise ConfigurationCheckError(location, 'unknown resource name')
    except RuleSyntaxError:
        raise ConfigurationCheckError(location, 'invalid rule or selector syntax')
    except NameError:
        raise ConfigurationCheckError(location, 'undefined template variable')
    except MonitoringError as error:
        message = str(error)
        safe = 'invalid configuration or expression'
        for prefix, description in (('invalid docker command', 'unsupported Docker action'),
                                    ('unknown alias', 'unknown rule alias'),
                                    ('missing command', 'empty container command'),
                                    ('unable to find container group', 'unknown container group')):
            if message.startswith(prefix):
                safe = description
                break
        raise ConfigurationCheckError(location, safe)
    except (IOError, OSError):
        raise ConfigurationCheckError(location, 'unable to read configuration or import file')
    except Exception as error:
        # Parser exceptions may embed rendered secrets or complete command lines.
        raise ConfigurationCheckError(location, 'invalid configuration or expression (%s)' % type(error).__name__)


class CheckedConfiguration(Configuration):
    """Extra diagnostics are opt-in; other commands retain their loader behavior."""
    def load(self, include_rules=True):
        self.source = (os.path.abspath(self.conffile) if os.path.exists(self.conffile)
                       else 'MONIT_DOCKER_CONFIG' if self.inline else os.path.abspath(self.conffile))
        self._root_pending = True
        require(os.path.exists(self.conffile) or bool(self.inline), self.source,
                'configuration file not found and MONIT_DOCKER_CONFIG is not set')
        return checked(self.source, lambda: super(CheckedConfiguration, self).load(include_rules))

    def _load_yaml(self, stream, loader=YamlLoader):
        result = super(CheckedConfiguration, self)._load_yaml(stream, loader)
        if self._root_pending:
            self._root_pending = False
            mapping(result, self.source)
            allowed = {'general', 'vars', 'clients', 'ctn-groups', 'dir-groups', 'conditions', 'commands'}
            require(not set(result) - allowed, self.source, 'unknown top-level section')
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
        required = {'client': 'config', 'ctn-group': 'match', 'dir-group': 'paths', 'condition': 'expr', 'command': 'exec'}[kind]
        for name, value in conf.items():
            location = '%s.%s' % (kind, name)
            if name.startswith('@'):
                require(name in ('@import_' + kind, '@import_vars'), location, 'unknown import directive')
                continue
            require(bool(name), kind, 'entry name must not be empty')
            if kind in ('condition', 'command', 'dir-group'):
                require(re.match(r'^[a-zA-Z][a-zA-Z0-9_.-]{0,64}$', name), location, 'invalid alias name')
            mapping(value, location)
            require(required in value, location, 'missing %s' % required)
            require(not set(value) - {required, 'vars', '@import_vars'}, location, 'unknown entry field')
            if 'vars' in value:
                mapping(value['vars'], location + '.vars')

    def _load_conf_section(self, xtype, section, conf, config_dir=None):
        def load():
            self._entries(conf, xtype)
            return super(CheckedConfiguration, self)._load_conf_section(xtype, section, conf, config_dir)
        return checked('%s: %s' % (self.source, xtype), load)


def _validate_action_list(values, location):
    require(isinstance(values, list) and bool(values), location, 'expected a nonempty action list')
    for index, value in enumerate(values):
        where = '%s[%s]' % (location, index)
        if isinstance(value, six.string_types):
            require(bool(value.strip()), where, 'empty action')
            continue
        mapping(value, where)
        require(len(value) == 1, where, 'an action mapping must contain exactly one command')
        options = next(iter(value.values()))
        mapping(options, where)
        require(not set(options) - {'args', 'kwargs'}, where, 'only args and kwargs are supported')
        if 'args' in options:
            require(isinstance(options['args'], list), where + '.args', 'expected a list')
        if 'kwargs' in options:
            mapping(options['kwargs'], where + '.kwargs')


def _validate_rule(parser, expression, location):
    def validate():
        rule = parser.parse(expression)
        # Validate each condition separately: normal evaluation short-circuits.
        values = dict((field, 1) for field in ContainerSnapshot.RESOURCE_FIELDS
                      if field not in STATE_RESOURCES)
        values.update(cpu_percent=1.0, mem_percent=1.0, pids_percent=1.0, health='healthy')
        values.update((field, 1) for field in EVENT_RESOURCES)
        values['filesystems'] = tuple(FilesystemSample(group, path, *([1.0] * len(FILESYSTEM_NUMERIC_FIELDS)), fs_mode='rw', fs_readable=1, fs_writable=1, fs_executable=1)
                                     for group, paths in parser.dir_groups.items() for path in paths)
        snapshot = ContainerSnapshot(status='running', pid=1, **values)
        for condition in rule.conditions:
            RuleEvaluator().matches(Rule(expression, (condition,), ()), snapshot)
        return rule
    return checked(location, validate)


def check_configuration(conffile, inline=None, selectors=None, selected_groups=(), client=None,
                        from_env=False, expressions=()):
    loader = CheckedConfiguration(conffile, inline)
    config = loader.load()
    for name, entry in config.get('clients', {}).items():
        mapping(entry['config'], 'clients.%s.config' % name)
        if 'tls' in entry['config']:
            tls = entry['config']['tls']
            require(isinstance(tls, (dict, bool)), 'clients.%s.config.tls' % name,
                    'expected a TLS mapping or boolean')
    if client and not from_env:
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
    return {'clients': len(config.get('clients', {})), 'groups': len(config.get('ctn-groups', {})),
            'commands': len(commands), 'conditions': len(conditions), 'rules': len(expressions)}
