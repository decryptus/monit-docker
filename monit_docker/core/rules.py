"""Evaluate normalized conditions using only transport-neutral snapshots."""

from monit_docker.domain.errors import MonitoringError, RuleSyntaxError


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
        return all(self._matches(condition, snapshot) for condition in rule.conditions)

    def _matches(self, condition, snapshot):
        actual = getattr(snapshot, condition.resource)
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
