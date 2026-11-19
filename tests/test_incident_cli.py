"""The real CLI must remain observation-only, with explicit model invocation."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from monit_docker.cli import argv_parse_check, main
from monit_docker.cli_incident import render_report
from monit_docker.incident import IncidentAssistant
from incident_fixtures import status, completion, provider_server, respond_json, IDENTIFIER, NOW, SECRET


class IncidentCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.status = self.root / 'status.json'
        self.status.write_text(json.dumps(status()))
        self.args = ['explain', '--status-file', str(self.status), '--container-id', IDENTIFIER, '--output', 'json']

    def run_cli(self, args):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(argv_parse_check(args))
        return code, output.getvalue(), errors.getvalue()

    def test_preview_does_not_read_provider_config_or_initialize_docker_runtime(self):
        with patch('monit_docker.adapters.incident_ai.load_provider', side_effect=AssertionError('provider read')), \
                patch('monit_docker.cli.initialize_runtime', side_effect=AssertionError('runtime initialized')), \
                patch('monit_docker.composition.client_factory', side_effect=AssertionError('Docker connected')):
            code, output, errors = self.run_cli(self.args + ['--preview', '--ai-config', '/nonexistent'])
        self.assertEqual(code, 0, errors)
        report = json.loads(output)
        self.assertEqual(report['status'], 'preview')
        self.assertNotIn(SECRET, output)

    def test_actual_cli_ollama_request_is_one_call_and_never_initializes_runtime(self):
        with provider_server(lambda h, p: respond_json(h, completion(provider='ollama'))) as (origin, requests):
            config = self.root / 'ai.yml'
            config.write_text(json.dumps(dict(provider='ollama', endpoint=origin + '/api/chat', model='fixture-model')))
            with patch('monit_docker.cli.initialize_runtime', side_effect=AssertionError('runtime initialized')):
                code, output, errors = self.run_cli(self.args + ['--ai-config', str(config), '--rule', 'mem_percent > 90 ? restart'])
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)['status'], 'completed')
        self.assertEqual(len(requests), 1)
        self.assertNotIn(SECRET, json.dumps(requests))

    def test_cli_errors_are_structured_and_never_contain_input_details(self):
        self.status.write_text('{"secret":"' + SECRET + '"')
        code, output, errors = self.run_cli(self.args + ['--preview'])
        self.assertEqual(code, 120)
        self.assertEqual(json.loads(output), {'status': 'error', 'reason': 'invalid_json'})
        self.assertNotIn(SECRET, output + errors)

    def test_invalid_rule_does_not_echo_its_secret(self):
        code, output, errors = self.run_cli(self.args + ['--preview', '--rule', SECRET])
        self.assertEqual(code, 120)
        self.assertNotIn(SECRET, output + errors)

    def test_global_selectors_are_not_silently_ignored(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            argv_parse_check(['--name', 'another-container'] + self.args + ['--preview'])

    def test_model_requires_explicit_configuration_and_bounded_options(self):
        for args in (self.args, self.args + ['--preview', '--max-age', '0'],
                     self.args + ['--preview', '--rule', 'x' * 3000]):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                argv_parse_check(args)

    def test_terminal_output_quotes_model_control_sequences(self):
        report = IncidentAssistant(clock=lambda: NOW).explain(status(), IDENTIFIER, preview=True)
        report.update(analysis={'hypotheses': [{'text': '\x1b[2J\n' + SECRET, 'evidence': ['e1']}]},
                      elapsed_ms=1, usage={})
        output = io.StringIO()
        render_report(report, output)
        self.assertNotIn('\x1b', output.getvalue())
        self.assertIn('\\u001b', output.getvalue())

    def test_optional_journal_is_filtered_by_exact_container(self):
        from monit_docker.audit import AuditJournal
        journal_path = self.root / 'events.jsonl'
        journal = AuditJournal(journal_path)
        journal.record('action', 'skipped', container_id=IDENTIFIER, container_name=SECRET,
                       action='restart', reason='restart-limit', result='skipped')
        journal.record('action', 'completed', container_id='b' * 64, result='succeeded', actor=SECRET)
        before = journal_path.read_bytes()
        code, output, errors = self.run_cli(['--audit-file', str(journal_path)] + self.args + ['--preview'])
        self.assertEqual(code, 0, errors)
        history = [e for e in json.loads(output)['context']['evidence'] if e['kind'] == 'action_history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['reason'], 'restart-limit')
        self.assertNotIn(SECRET, output)
        self.assertEqual(journal_path.read_bytes(), before)
