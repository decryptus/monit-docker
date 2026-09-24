"""Evaluate normalized conditions using only transport-neutral snapshots."""

from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.filesystems import filesystem_resource
from monit_docker.domain.runtime import RUNTIME_RESOURCES


class RuleEvaluator(object):
    @staticmethod
    def _compare(operator, actual, expected, membership=False):
        if operator == '==':
            return actual == expected
        if operator == '!=':
            return actual != expected
        if operator == '>=':
            return actual >= expected
        if operator == '<=':
            return actual <= expected
        if operator == '>':
            return actual > expected
        if operator == '<':
            return actual < expected
        if operator == 'in' and membership:
            return actual in expected
        if operator == 'not in' and membership:
            return actual not in expected
        raise RuleSyntaxError('conditional operator unknown: %r' % operator)

    def matches(self, rule, snapshot):
        groups = {}
        for condition in rule.conditions:
            filesystem = filesystem_resource(condition.resource)
            if filesystem:
                field, group = filesystem
                groups.setdefault(group, []).append((field, condition))
            elif not self._matches(condition, snapshot):
                return False
        for group, conditions in groups.items():
            samples = [sample for sample in snapshot.filesystems if sample.group == group]
            if not samples:
                raise MonitoringError(115, 'no filesystem samples for directory group: %s' % group)
            # Missing inode accounting must never look like a healthy zero.
            if any(getattr(sample, field) is None for sample in samples for field, _ in conditions):
                raise MonitoringError(115, 'filesystem metric unavailable for directory group: %s' % group)
            if not any(all(self._matches_value(condition, getattr(sample, field))
                           for field, condition in conditions) for sample in samples):
                return False
        return True

    def _matches(self, condition, snapshot):
        actual = getattr(snapshot, condition.resource)
        return self._matches_value(condition, actual)

    def _matches_value(self, condition, actual):
        if condition.resource in RUNTIME_RESOURCES and actual is None:
            raise MonitoringError(115, 'runtime metric unavailable: %s' % condition.resource)
        if condition.resource == 'pid':
            actual = actual or ''  # Preserve the legacy missing-PID comparison.
        operator = condition.operator.strip()
        pre_value = condition.pre_value
        if operator in ('in', 'not in'):
            value = condition.value
            if not (value.startswith('(') and value.endswith(')')):
                raise RuleSyntaxError('invalid value with %r for expression if: %r' %
                                      (operator, condition.source))
            expected = value[1:-1].split(',')
        else:
            try:
                expected = type(actual)(condition.value)
                if pre_value is not None:
                    pre_value = type(actual)(pre_value)
            except TypeError:
                raise MonitoringError(110, 'invalid value for expression if: %r' %
                                      condition.source)
        if None not in (condition.pre_operator, pre_value):
            # Deliberately retain historical precondition operand order.
            return (self._compare(condition.pre_operator, actual, pre_value) and
                    self._compare(operator, actual, expected))
        return self._compare(operator, actual, expected, membership=True)
