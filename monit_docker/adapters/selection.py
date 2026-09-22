"""Compile legacy container selectors, then match plain container attributes."""

import fnmatch
import re

import six

from monit_docker.adapters.syntax import CTN_GRP_MATCH
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError


class ContainerSelector(object):
    def __init__(self, selectors=None, statuses=(), groups=None, selected_groups=()):
        self.statuses = tuple(statuses)
        self.patterns = dict((name, []) for name in ('id', 'image', 'name', 'label'))
        compiled_groups = {}
        for name, group in (groups or {}).items():
            compiled = []
            for expression in group['match']:
                match = CTN_GRP_MATCH(expression)
                if not match:
                    raise RuleSyntaxError('unable to parse container group match: %r' % expression)
                kind, pattern = match.group('subset', 'pattern')
                compiled.append((kind, self._compile(kind, pattern)))
            compiled_groups[name] = compiled
        if selected_groups:
            for name in selected_groups:
                if name not in compiled_groups:
                    raise MonitoringError(110, 'unable to find container group: %r' % name)
                for kind, pattern in compiled_groups[name]:
                    self.patterns[kind].append(pattern)
        else:
            for kind, values in (selectors or {}).items():
                for value in self._split(values):
                    self.patterns[kind].append(self._compile(kind, value))

    @staticmethod
    def _compile(kind, pattern):
        if kind == 'id' and len(pattern) == 12 and pattern.isalnum():
            pattern += '*'
        return re.compile(pattern[1:] if pattern.startswith('~') else fnmatch.translate(pattern)).match

    @classmethod
    def _split(cls, values):
        if isinstance(values, list):
            return [value for item in values for value in cls._split(item)]
        if isinstance(values, bytes):
            values = values.decode('utf-8', 'replace')
        elif not isinstance(values, six.string_types):
            values = six.text_type(values)
        return [value.strip() for value in values.split(',')]

    def matches(self, identifier, name, status, labels, image_tags):
        if self.statuses and status not in self.statuses:
            return False
        if not any(self.patterns.values()):
            return True
        attributes = {'id': (identifier,), 'name': (name,),
                      'label': labels, 'image': image_tags}
        return any(pattern(value) for kind, patterns in self.patterns.items()
                   for pattern in patterns for value in attributes[kind])
