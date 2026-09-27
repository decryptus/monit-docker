"""Compile container selectors and intersect direct patterns with selected groups."""

import fnmatch
import re

import regex
import six

from monit_docker.adapters.syntax import CTN_GRP_MATCH
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError

_SELECTOR_KINDS = ('id', 'image', 'name', 'label')
_MAX_PATTERN_LENGTH = 4096
_REGEX_TIMEOUT = 0.05


class ContainerSelector(object):
    def __init__(self, selectors=None, statuses=(), groups=None, selected_groups=()):
        self.statuses = tuple(statuses)
        self.patterns = dict((name, []) for name in _SELECTOR_KINDS)
        self.group_patterns = dict((name, []) for name in _SELECTOR_KINDS)
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
        for name in selected_groups:
            if name not in compiled_groups:
                raise MonitoringError(110, 'unable to find container group: %r' % name)
            for kind, pattern in compiled_groups[name]:
                self.group_patterns[kind].append(pattern)
        for kind, values in (selectors or {}).items():
            for value in self._split(values):
                self.patterns[kind].append(self._compile(kind, value))

    @staticmethod
    def _compile(kind, pattern):
        if len(pattern) > _MAX_PATTERN_LENGTH:
            raise MonitoringError(110, 'selector pattern exceeds length limit')
        if kind == 'id' and len(pattern) == 12 and pattern.isalnum():
            pattern += '*'
        if not pattern.startswith('~'):
            return re.compile(fnmatch.translate(pattern)).match
        # Keep Python regex syntax; use a bounded matcher for untrusted patterns.
        re.compile(pattern[1:])
        try:
            compiled = regex.compile(pattern[1:], regex.VERSION0)
        except regex.error:
            raise MonitoringError(110, 'invalid selector regular expression') from None
        def match(value):
            try:
                return compiled.match(value, timeout=_REGEX_TIMEOUT)
            except TimeoutError:
                raise MonitoringError(110, 'selector regular expression timed out') from None
        return match

    @classmethod
    def _split(cls, values):
        # Repeat the option or supply a YAML list; commas belong to the pattern.
        if isinstance(values, (list, tuple)):
            return [value for item in values for value in cls._split(item)]
        if isinstance(values, bytes):
            values = values.decode('utf-8', 'replace')
        elif not isinstance(values, six.string_types):
            values = six.text_type(values)
        return [values.strip()]

    @staticmethod
    def _matches_patterns(patterns, attributes):
        return not any(patterns.values()) or any(
            pattern(value) for kind, entries in patterns.items()
            for pattern in entries for value in attributes[kind])

    def matches(self, identifier, name, status, labels, image_tags):
        if self.statuses and status not in self.statuses:
            return False
        attributes = {'id': (identifier,), 'name': (name,),
                      'label': labels, 'image': image_tags}
        return (self._matches_patterns(self.group_patterns, attributes) and
                self._matches_patterns(self.patterns, attributes))
