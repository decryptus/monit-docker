"""YAML defaults for the direct serve subcommand."""
import ipaddress
import sys

from monit_docker.adapters.syntax import RESOURCE_CHOICES
from monit_docker.adapters.validation_rules import mapping, require, string_list
from monit_docker.domain.filesystems import filesystem_resource

_BOOL_FIELDS = frozenset(('allow_actions', 'allow_maintenance', 'trust_proxy_user'))
_INTEGER_MIN = {'port': 1, 'max_restarts': 1}
_NUMBER_MIN = {'interval': 0.1, 'stale_after': 0.1, 'cooldown': 0, 'action_cooldown': 1,
               'trigger_after': 0, 'max_gap': 0}
_TEXT_FIELDS = frozenset(('bind', 'state_file', 'action_origin', 'action_token_file',
                          'audit_read_token_file', 'notification_token_file', 'metrics_token_file',
                          'status_token_file'))
_LIST_FIELDS = frozenset(('rsc', 'cmd'))
SERVE_FIELDS = _BOOL_FIELDS | _INTEGER_MIN.keys() | _NUMBER_MIN.keys() | _TEXT_FIELDS | _LIST_FIELDS


def validate_serve_section(section):
    mapping(section, 'serve')
    require(not set(section) - SERVE_FIELDS, 'serve', 'unknown serve option')
    for key, value in section.items():
        where = 'serve.' + key
        if key in _BOOL_FIELDS:
            require(type(value) is bool, where, 'expected a boolean')
        elif key in _INTEGER_MIN:
            require(type(value) is int and value >= _INTEGER_MIN[key], where, 'expected a positive integer')
        elif key in _NUMBER_MIN:
            require(type(value) in (int, float) and value >= _NUMBER_MIN[key]
                    and value <= sys.float_info.max, where, 'invalid duration')
        elif key in _LIST_FIELDS:
            string_list(value, where)
        else:
            require(isinstance(value, str) and bool(value.strip()) and '\x00' not in value,
                    where, 'expected a nonempty string')
    if 'bind' in section:
        try:
            ipaddress.IPv4Address(section['bind'])
        except ValueError:
            require(False, 'serve.bind', 'expected an IPv4 address')
    require(section.get('port', 1) <= 65535, 'serve.port', 'port must be at most 65535')
    for resource in section.get('rsc', ()):
        require(resource in RESOURCE_CHOICES or filesystem_resource(resource), 'serve.rsc', 'unknown resource')
