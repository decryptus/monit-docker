"""Offline configuration checks using the runtime loader and rule grammar."""
import re

import six
import yaml

from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError, ResourceTypeError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot, STATE_RESOURCES
from monit_docker.domain.runtime import EVENT_RESOURCES
from monit_docker.domain.rules import Rule
from monit_docker.domain.filesystems import FilesystemSample, FILESYSTEM_NUMERIC_FIELDS


ACTION_OPTION_KEYS = frozenset(('args', 'kwargs'))

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
        require(not set(options) - ACTION_OPTION_KEYS, where, 'only args and kwargs are supported')
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
