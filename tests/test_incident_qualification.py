"""Qualification opt-in, synthetic isolation, call budgets and honest outcomes."""
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import Mock

from monit_docker.incident import ExplanationError
from incident_fixtures import NOW, analysis

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATHS = (ROOT / '.github/scripts/qualify-incident-ai.py', ROOT / 'scripts/qualify-incident-ai.py')
SCRIPT = next((path for path in SCRIPT_PATHS if path.is_file()), None)
if SCRIPT is None:
    raise RuntimeError('Qualification script is missing from the test environment')
SPEC = importlib.util.spec_from_file_location('incident_qualification', SCRIPT)
QUALIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(QUALIFY)


def provider():
    result = Mock()
    result.config = dict(provider='openai-compatible', model='synthetic-test-model',
                         max_output_tokens=1024, timeout=30, api_key_file='not-for-report')
    result.complete.return_value = dict(analysis=analysis(), usage=dict(prompt_tokens=1000, completion_tokens=100))
    return result


class IncidentQualificationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name) / 'report.json'
        self.stdout, self.stderr = io.StringIO(), io.StringIO()

    def invoke(self, args=(), loader=None):
        with redirect_stdout(self.stdout), redirect_stderr(self.stderr):
            return QUALIFY.main(['--output', str(self.output), *args], provider_loader=loader)

    def test_preview_covers_eight_cases_without_loading_provider_or_key(self):
        loader = Mock(side_effect=AssertionError('Provider must not be loaded for preview'))
        self.assertEqual(self.invoke(['--ai-config', '/nonexistent/private.yml'], loader), 0)
        loader.assert_not_called()
        result = json.loads(self.output.read_text())
        self.assertEqual(result['calls_attempted'], 0)
        self.assertEqual(len(result['cases']), 8)
        self.assertEqual(result['qualification'], 'not_established')
        self.assertTrue(result['human_review_required'])
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertNotIn(QUALIFY.CANARY, self.output.read_text())
        self.assertNotIn(QUALIFY.IDENTIFIER, self.output.read_text())
        cases = {row['name']: row for row in result['cases']}
        self.assertEqual(cases['stale-snapshot']['context']['freshness'], 'historical_or_unknown')
        self.assertEqual(cases['memory-pressure']['context']['freshness'], 'current')
        self.assertTrue(any('partial' in note for note in cases['partial-history']['context']['missing_evidence']))

    def test_live_requires_explicit_sufficient_call_budget_before_key_loading(self):
        loader = Mock()
        with self.assertRaises(SystemExit):
            self.invoke(['--live', '--ai-config', 'private.yml'], loader)
        loader.assert_not_called()
        self.assertFalse(self.output.exists())
        with self.assertRaises(SystemExit):
            self.invoke(['--live', '--case', 'memory-pressure'], loader)
        loader.assert_not_called()

    def test_live_keeps_provider_choice_and_does_not_claim_model_quality(self):
        model = provider()
        loader = Mock(return_value=model)
        self.assertEqual(self.invoke(['--live', '--ai-config', 'private.yml', '--case', 'memory-pressure'], loader), 0)
        model.complete.assert_called_once()
        encoded = self.output.read_text()
        result = json.loads(encoded)
        self.assertEqual(result['configured_model'], 'synthetic-test-model')
        self.assertFalse(result['model_revision_verified'])
        self.assertEqual(result['qualification'], 'not_established')
        self.assertEqual(result['run_status'], 'awaiting_human_review')
        self.assertEqual(result['cases'][0]['human_verdict'], 'pending')
        self.assertIsNone(result['cases'][0]['estimated_usd'])
        self.assertNotIn('not-for-report', encoded)
        sent = json.dumps(model.complete.call_args.args[1])
        self.assertNotIn(QUALIFY.CANARY, sent)
        self.assertNotIn(QUALIFY.IDENTIFIER, sent)

    def test_failure_stops_later_calls_and_does_not_invent_usage_or_cost(self):
        model = provider()
        model.complete.side_effect = ExplanationError('provider_timeout')
        args = ['--live', '--ai-config', 'private.yml', '--max-calls', '2',
                '--case', 'memory-pressure', '--case', 'restart-budget']
        self.assertEqual(self.invoke(args, Mock(return_value=model)), 1)
        model.complete.assert_called_once()
        result = json.loads(self.output.read_text())
        self.assertEqual([r['status'] for r in result['cases']], ['error', 'skipped'])
        self.assertFalse(result['reported_usage_complete'])
        self.assertFalse(result['billing_cost_verified'])
        self.assertNotIn('estimated_usd', result['cases'][0])

    def test_existing_output_or_symlink_is_rejected_before_loading_credentials(self):
        self.output.write_text('preserve-private-input')
        loader = Mock()
        self.assertEqual(self.invoke(['--live', '--ai-config', 'private.yml', '--case', 'sparse-evidence'], loader), 2)
        self.assertEqual(self.output.read_text(), 'preserve-private-input')
        loader.assert_not_called()
        self.output.unlink()
        target = Path(self.directory.name) / 'key'
        target.write_text('preserve-key')
        self.output.symlink_to(target)
        self.assertEqual(self.invoke(loader=loader), 2)
        self.assertEqual(target.read_text(), 'preserve-key')

    def test_duplicate_cases_and_invalid_rates_are_rejected_without_requests(self):
        loader = Mock()
        for args in (['--case', 'sparse-evidence', '--case', 'sparse-evidence'],
                     ['--input-usd-per-million', 'NaN'], ['--max-calls', '100'],
                     ['--input-usd-per-million', '1']):
            with self.assertRaises(SystemExit):
                self.invoke(args, loader)
        loader.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_cost_is_only_an_explicit_estimate_using_both_reported_counts(self):
        self.assertEqual(QUALIFY.estimate({'prompt_tokens': 1000, 'completion_tokens': 100},
                         (Decimal('0.15'), Decimal('0.60'))), '0.00021')
        self.assertIsNone(QUALIFY.estimate({'prompt_tokens': 1000}, (Decimal('1'), Decimal('2'))))
        self.assertIsNone(QUALIFY.estimate({'prompt_tokens': True, 'completion_tokens': 1},
                                         (Decimal('1'), Decimal('2'))))

    def test_interrupted_request_has_a_checkpoint_and_is_not_retried(self):
        model = provider()
        model.complete.side_effect = KeyboardInterrupt()
        self.assertEqual(self.invoke(['--live', '--ai-config', 'private.yml', '--case', 'sparse-evidence'],
                                    Mock(return_value=model)), 130)
        model.complete.assert_called_once()
        result = json.loads(self.output.read_text())
        self.assertEqual(result['cases'][0]['status'], 'interrupted')
        self.assertFalse(result['reported_usage_complete'])

    def test_checkpoints_mark_request_uncertain_before_contacting_provider(self):
        checkpoints = []
        model = provider()
        def complete(prompt, context):
            self.assertEqual(checkpoints[-1]['cases'][0]['status'], 'request_in_progress')
            self.assertFalse(checkpoints[-1]['reported_usage_complete'])
            return dict(analysis=analysis(), usage={})
        model.complete.side_effect = complete
        result = QUALIFY.qualify(QUALIFY.scenarios(NOW)[:1], now=NOW, provider=model,
            checkpoint=lambda value: checkpoints.append(json.loads(json.dumps(value))))
        self.assertFalse(result['reported_usage_complete'])
        self.assertEqual(result['qualification'], 'not_established')

    def test_french_qualification_records_the_actual_prompt(self):
        import hashlib
        from monit_docker.incident import explanation_prompt
        self.assertEqual(self.invoke(['--language', 'fr']), 0)
        report = json.loads(self.output.read_text())
        self.assertEqual(report['language'], 'fr')
        self.assertEqual(report['prompt_sha256'], hashlib.sha256(explanation_prompt('fr').encode()).hexdigest())


if __name__ == '__main__':
    unittest.main()
