"""Rule literals that cannot be compared must fail as documented errors.

An unsupported size unit or a literal that does not fit the measured value's
type used to escape as an unhandled ValueError, so the historical runtime CLI
reported the reserved "unexpected exception" status instead of a rule or value
error.
"""
import unittest

from monit_docker.adapters.rules import RuleParser
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot


class RuleLiteralTests(unittest.TestCase):
    def test_unsupported_unit_is_a_rule_syntax_error(self):
        for expression in ('mem_usage > 100 mb ? restart',
                           'mem_limit > 100 KB ? restart',
                           '100 mb < mem_usage < 200 MiB ? restart'):
            with self.subTest(expression=expression):
                with self.assertRaises(RuleSyntaxError):
                    RuleParser().parse(expression)

    def test_unsupported_unit_in_a_configured_condition_is_a_rule_syntax_error(self):
        with self.assertRaises(RuleSyntaxError):
            RuleParser(conditions={'heavy': {'expr': ['mem_usage > 100 mb']}})

    def test_value_that_does_not_fit_the_resource_is_a_value_error(self):
        parser = RuleParser()
        snapshot = ContainerSnapshot(mem_usage=1048576, mem_limit=8388608,
                                     cpu_percent=12.5, mem_percent=25.0)
        for expression in ('mem_usage > 1.5 ? restart',
                           '1.5 < mem_usage < 2048 ? restart',
                           'cpu_percent > healthy ? restart'):
            with self.subTest(expression=expression):
                with self.assertRaises(MonitoringError) as error:
                    RuleEvaluator().matches(parser.parse(expression), snapshot)
                self.assertEqual(error.exception.code, 110)

    def test_supported_units_and_integer_values_still_evaluate(self):
        parser = RuleParser()
        snapshot = ContainerSnapshot(mem_usage=209715200, mem_limit=8388608,
                                     cpu_percent=12.5, mem_percent=25.0)
        self.assertTrue(RuleEvaluator().matches(
            parser.parse('mem_usage > 100 MiB ? restart'), snapshot))
        self.assertTrue(RuleEvaluator().matches(
            parser.parse('100 MiB < mem_usage < 300 MiB ? restart'), snapshot))
        self.assertTrue(RuleEvaluator().matches(
            parser.parse('cpu_percent > 1.5 ? restart'), snapshot))


if __name__ == '__main__':
    unittest.main()
