"""Evidence minimization, freshness and application capability boundaries."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock

import monit_docker
from monit_docker.incident import (IncidentAssistant, ExplanationError, build_context, validate_analysis,
                                   MAX_EVENTS, MAX_ITEMS, MAX_TEXT)
from monit_docker.adapters.rules import RuleParser
from incident_fixtures import status, analysis, IDENTIFIER, NOW, SECRET


class IncidentContextTests(unittest.TestCase):
    def context(self, data=None, **kwargs):
        return build_context(status() if data is None else data, IDENTIFIER, now=NOW, **kwargs)

    def test_context_excludes_identifiers_names_free_text_other_containers_and_secrets(self):
        data = status()
        data.update(last_error=SECRET, failed_action={'command': SECRET}, environment={'key': SECRET})
        data['containers'][0].update(labels={'token': SECRET}, env=[SECRET], logs=SECRET,
                                      command=SECRET, filesystems=[{'path': SECRET}])
        data['containers'].append({'id': 'b' * 64, 'name': SECRET, 'mem_percent': 13.37})
        rule = RuleParser().parse('mem_percent > 90 ? (echo ' + SECRET + ')')
        context = self.context(data, rules=[rule])
        encoded = json.dumps(context)
        for forbidden in (SECRET, IDENTIFIER, 'b' * 64, '13.37', 'echo'):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(context['target'], 'container-1')
        self.assertEqual(context['evidence'][-1]['actions'], ['opaque_command'])
        self.assertEqual(data['containers'][0]['name'], SECRET)

    def test_current_and_retained_failed_cycle_are_distinguished(self):
        self.assertEqual(self.context()['freshness'], 'current')
        data = status()
        data.update(ready=False, last_cycle_success=False,
                    last_complete_snapshot={'collected_at': NOW - 200, 'containers': data['containers']}, containers=[])
        context = self.context(data)
        self.assertEqual(context['freshness'], 'historical_or_unknown')
        self.assertEqual(context['age_seconds'], 200)

    def test_saved_snapshot_cannot_remain_fresh_using_its_old_age_field(self):
        data = status()
        data['last_success_at'] = NOW - 10000
        self.assertEqual(self.context(data)['freshness'], 'historical_or_unknown')
        for value in (NOW + 100, None, 'yesterday', float('nan')):
            data['last_success_at'] = value
            self.assertEqual(self.context(data)['freshness'], 'historical_or_unknown')

    def test_invalid_or_missing_measurements_are_not_facts(self):
        for value in (True, '96', -1, float('inf'), 10 ** 500, {'secret': SECRET}):
            data = status()
            data['containers'][0]['mem_percent'] = value
            self.assertFalse(any(e.get('field') == 'mem_percent' for e in self.context(data)['evidence']))
        data['containers'][0]['status'] = SECRET
        self.assertNotIn(SECRET, json.dumps(self.context(data)))

    def test_protections_and_restart_budget_are_explicit_evidence(self):
        facts = {item['field']: item['value'] for item in self.context()['evidence']}
        self.assertTrue(facts['maintenance_active'])
        self.assertTrue(facts['manual_actions_protected'])
        self.assertEqual(facts['restart_attempts'], facts['restart_limit'])

    def test_exact_selection_and_status_schema_fail_closed(self):
        for identifier in ('a' * 63, IDENTIFIER + '\n', 'é' * 64, '*'):
            with self.assertRaisesRegex(ExplanationError, 'invalid_container_id'):
                build_context(status(), identifier, now=NOW)
        for version in (None, True, 2, '1'):
            data = status()
            data['api_version'] = version
            with self.assertRaisesRegex(ExplanationError, 'invalid_status'):
                self.context(data)
        data = status()
        data['containers'].append(copy.deepcopy(data['containers'][0]))
        with self.assertRaisesRegex(ExplanationError, 'ambiguous_container'):
            self.context(data)
        data['containers'] = []
        with self.assertRaisesRegex(ExplanationError, 'container_not_found'):
            self.context(data)

    def test_journal_is_exact_filtered_bounded_and_allowlisted(self):
        record = dict(container_id=IDENTIFIER, category='action', event='skipped', result='skipped',
                      action='restart', reason='restart-limit', timestamp='2026-11-19T07:00:00Z',
                      host=SECRET, actor=SECRET, command_id=SECRET, rule_id=SECRET, command=SECRET)
        records = [record.copy() for _ in range(MAX_EVENTS + 1)]
        records.append(dict(record, container_id='b' * 64, reason=SECRET))
        context = self.context(records=records, history_more=True)
        history = [item for item in context['evidence'] if item['kind'] == 'action_history']
        self.assertEqual(len(history), MAX_EVENTS)
        self.assertEqual(history[0]['reason'], 'restart-limit')
        self.assertNotIn(SECRET, json.dumps(context))
        self.assertTrue(any('partial' in item for item in context['missing_evidence']))

    def test_invalid_journal_timestamp_is_not_transmitted(self):
        context = self.context(records=[dict(container_id=IDENTIFIER, category='action', timestamp=SECRET)])
        self.assertNotIn(SECRET, json.dumps(context))
        self.assertTrue(any('invalid timestamp' in item for item in context['missing_evidence']))

    def test_supplied_rule_describes_conditions_without_claiming_activation(self):
        rules = [RuleParser().parse('80 < mem_percent <= 99 ? restart'),
                 RuleParser().parse('status in (running,exited) ? stop')]
        context = self.context(rules=rules)
        entries = [e for e in context['evidence'] if e['kind'] == 'operator_supplied_rule']
        self.assertEqual(entries[0]['conditions'][0]['pre_value'], 80)
        self.assertEqual(entries[1]['conditions'][0]['value'], ['running', 'exited'])
        self.assertIn('Supplied rules are not verified as the active configuration.', context['missing_evidence'])

    def test_preview_never_calls_provider_and_preserves_input(self):
        provider = Mock()
        before = status()
        data = copy.deepcopy(before)
        report = IncidentAssistant(provider, clock=lambda: NOW).explain(data, IDENTIFIER, preview=True)
        provider.complete.assert_not_called()
        self.assertEqual(data, before)
        self.assertEqual(report['status'], 'preview')

    def test_invalid_context_is_rejected_before_provider_call(self):
        provider = Mock()
        with self.assertRaises(ExplanationError):
            IncidentAssistant(provider).explain({}, IDENTIFIER)
        provider.complete.assert_not_called()

    def test_one_request_with_usage_and_separate_model_hypotheses(self):
        provider = Mock()
        provider.complete.return_value = dict(analysis=analysis(), usage={'total_tokens': 260})
        report = IncidentAssistant(provider, clock=lambda: NOW).explain(status(), IDENTIFIER)
        self.assertEqual(provider.complete.call_count, 1)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['usage']['total_tokens'], 260)
        self.assertIn('evidence', report['context'])
        self.assertNotIn('facts', report['analysis'])

    def test_unknown_references_and_executable_output_fields_are_rejected(self):
        context = self.context()
        for refs in ([], ['invented'], [dict(secret=SECRET)], ['e1'] * 9):
            value = analysis()
            value['hypotheses'][0]['evidence'] = refs
            with self.assertRaisesRegex(ExplanationError, 'invalid_evidence_reference'):
                validate_analysis(value, context)
        value = dict(analysis(), commands=['docker restart x'])
        with self.assertRaisesRegex(ExplanationError, 'invalid_model_output'):
            validate_analysis(value, context)

    def test_model_lists_and_strings_are_bounded(self):
        context = self.context()
        for value in (None, [], {'hypotheses': []}, dict(analysis(), missing_evidence=['x'] * (MAX_ITEMS + 1)),
                      dict(analysis(), missing_evidence=['x' * (MAX_TEXT + 1)])):
            with self.assertRaises(ExplanationError):
                validate_analysis(value, context)

    def test_model_failure_does_not_modify_monitoring_state_or_retry(self):
        from monit_docker.service import MonitorService
        from monit_docker.domain.rules import CycleResult
        from monit_docker.domain.models import ContainerSnapshot
        monitor = MonitorService(lambda callback: CycleResult((ContainerSnapshot(id=IDENTIFIER),), ()))
        monitor.run_cycle()
        provider = Mock()
        provider.complete.side_effect = ExplanationError('provider_timeout')
        with self.assertRaisesRegex(ExplanationError, 'provider_timeout'):
            IncidentAssistant(provider).explain(monitor.status(), IDENTIFIER)
        monitor.run_cycle()
        self.assertTrue(monitor.status()['ready'])
        self.assertEqual(monitor.status()['cycles_total'], 2)
        self.assertEqual(provider.complete.call_count, 1)

    def test_service_initializes_and_calls_provider_with_interfaces_and_adapters_blocked(self):
        root = Path(monit_docker.__file__).resolve().parent.parent
        program = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith(('monit_docker.cli', 'monit_docker.adapters', 'docker', 'httpdis', 'requests', 'pycurl', 'textual', 'curses')):
   raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from monit_docker.incident import IncidentAssistant
class Provider:
 def complete(self, prompt, context):
  return {'analysis': {'hypotheses': [], 'missing_evidence': ['No measurements.'], 'suggested_checks': []}}
report = IncidentAssistant(Provider(), clock=lambda: 100).explain(
 {'api_version':1,'containers':[{'id':'a'*64}]}, 'a'*64)
assert report['status'] == 'completed'
'''
        result = subprocess.run([sys.executable, '-c', program], cwd=root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
