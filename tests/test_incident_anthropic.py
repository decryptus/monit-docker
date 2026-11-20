"""Native Claude wire contract, shared service validation and failure boundaries."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from monit_docker.adapters.incident_ai import ChatProvider, load_provider
from monit_docker.incident import IncidentAssistant, ExplanationError
from incident_fixtures import (IDENTIFIER, NOW, SECRET, analysis, completion,
                              provider_server, respond_json, status)


def config(origin='http://127.0.0.1:11434'):
    return dict(provider='anthropic', endpoint=origin + '/v1/messages',
                model='fixture-claude-model', timeout=3, max_output_tokens=1024)


class IncidentAnthropicTests(unittest.TestCase):
    def test_native_wire_contract_uses_private_header_and_filtered_service_context(self):
        with provider_server(lambda h, p: respond_json(h, completion(provider='anthropic'))) as (origin, requests):
            result = IncidentAssistant(ChatProvider(config(origin), SECRET), clock=lambda: NOW).explain(
                status(), IDENTIFIER)
        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(request['path'], '/v1/messages')
        self.assertEqual(request['authorization'], 'Bearer ' + SECRET)
        self.assertEqual(request['anthropic_version'], '2026-10-21')
        body = request['payload']
        self.assertEqual(set(body), {'model', 'system', 'messages', 'stream', 'max_tokens'})
        self.assertEqual(body['max_tokens'], 1024)
        self.assertFalse(body['stream'])
        self.assertIn('JSON', body['system'])
        self.assertEqual([m['role'] for m in body['messages']], ['user'])
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn(IDENTIFIER, json.dumps(body))
        self.assertEqual(result['analysis'], analysis())
        self.assertEqual(result['usage'], dict(prompt_tokens=200, completion_tokens=60))

    def test_missing_key_and_wrong_route_fail_before_network(self):
        transport = Mock()
        with self.assertRaisesRegex(ExplanationError, 'api_key_required'):
            ChatProvider(config(), transport=transport)
        with self.assertRaisesRegex(ExplanationError, 'invalid_provider_route'):
            ChatProvider(dict(config(), endpoint='http://127.0.0.1/v1/chat/completions'), SECRET,
                         transport=transport)
        transport.assert_not_called()

    def test_native_configuration_loads_relative_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'key'
            key.write_text(SECRET)
            key.chmod(0o600)
            path = root / 'ai.yml'
            path.write_text(json.dumps(dict(config(), api_key_file='key')))
            provider = load_provider(path)
            self.assertEqual(provider.config['provider'], 'anthropic')
            self.assertEqual(provider._token, SECRET)
            key.chmod(0o644)
            with self.assertRaisesRegex(ExplanationError, 'unsafe_secret_file'):
                load_provider(path)

    def test_refusal_truncation_tool_use_and_pause_never_become_success(self):
        for reason in ('max_tokens', 'refusal', 'tool_use', 'pause_turn', 'stop_sequence', None, 'future_reason'):
            response = completion(provider='anthropic')
            response['stop_reason'] = reason
            transport = Mock(return_value=response)
            with self.subTest(reason=reason), self.assertRaisesRegex(ExplanationError, 'incomplete_model_output'):
                ChatProvider(config(), SECRET, transport).complete('JSON', {})
            transport.assert_called_once()

    def test_malformed_or_nontext_blocks_are_rejected_without_discarding_content(self):
        changes = [dict(type='error'), dict(role='user'), dict(content=[]), dict(content=None),
                   dict(content=[dict(type='tool_use', name='restart')]), dict(content=[None]),
                   dict(content=[dict(type='text', text='not JSON')]),
                   dict(content=[dict(type='text', text='{}'), dict(type='thinking', thinking='hidden')]),
                   dict(content=[dict(type='text', text='x' * 16001)])]
        for change in changes:
            response = dict(completion(provider='anthropic'), **change)
            with self.subTest(change=list(change)), self.assertRaises(ExplanationError):
                ChatProvider(config(), SECRET, Mock(return_value=response)).complete('JSON', {})

    def test_native_results_still_require_valid_evidence_and_cannot_echo_key(self):
        unsafe = completion(dict(analysis(), missing_evidence=[SECRET]), provider='anthropic')
        with self.assertRaisesRegex(ExplanationError, 'unsafe_model_output'):
            ChatProvider(config(), SECRET, Mock(return_value=unsafe)).complete('JSON', {})
        value = analysis()
        value['hypotheses'][0]['evidence'] = ['invented-evidence']
        model = ChatProvider(config(), SECRET, Mock(return_value=completion(value, provider='anthropic')))
        with self.assertRaises(ExplanationError):
            IncidentAssistant(model, clock=lambda: NOW).explain(status(), IDENTIFIER)

    def test_usage_missing_invalid_or_cached_counts_do_not_invent_complete_accounting(self):
        for counts, expected in (
            ({}, {}),
            (dict(input_tokens=True, output_tokens=-1), {}),
            (dict(input_tokens=10, output_tokens=20, secret=SECRET), dict(prompt_tokens=10, completion_tokens=20)),
            (dict(input_tokens=10, output_tokens=20, cache_read_input_tokens=40), dict(completion_tokens=20)),
            (dict(input_tokens=10, output_tokens=20, cache_creation_input_tokens=True), dict(completion_tokens=20)),
        ):
            response = dict(completion(provider='anthropic'), usage=counts)
            with self.subTest(counts=counts):
                result = ChatProvider(config(), SECRET, Mock(return_value=response)).complete('JSON', {})
                self.assertEqual(result['usage'], expected)

    def test_native_http_errors_and_redirects_never_retry_or_expose_body(self):
        for code in (302, 401, 429, 529):
            with self.subTest(code=code):
                def reject(handler, payload):
                    handler.send_response(code)
                    handler.send_header('Location', '/steal-key')
                    handler.end_headers()
                    handler.wfile.write(SECRET.encode())
                with provider_server(reject) as (origin, requests):
                    with self.assertRaisesRegex(ExplanationError, '^provider_http_error$'):
                        ChatProvider(config(origin), SECRET).complete('JSON', {})
                self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
