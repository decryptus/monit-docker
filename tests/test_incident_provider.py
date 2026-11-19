"""Actual loopback HTTP protocol, provider failures and credential boundaries."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from monit_docker.adapters.incident_ai import (ChatProvider, load_provider, load_status, request_json,
    validate_url, read_file, decode_json, MAX_RESPONSE_BYTES)
from monit_docker.incident import ExplanationError
from incident_fixtures import status, completion, analysis, provider_server, respond_json, SECRET


class IncidentProviderTests(unittest.TestCase):
    def config(self, origin='http://127.0.0.1:11434', provider='ollama'):
        return dict(provider=provider, endpoint=origin + ('/api/chat' if provider == 'ollama' else '/v1/chat/completions'),
                    model='fixture-model', timeout=3, max_output_tokens=1024)

    def test_real_ollama_wire_contract_nonstreaming_and_usage(self):
        with provider_server(lambda h, p: respond_json(h, completion(provider='ollama'))) as (origin, requests):
            result = ChatProvider(self.config(origin)).complete('Return JSON.', {'evidence': []})
        self.assertEqual(result['usage'], {'prompt_tokens': 200, 'completion_tokens': 60})
        self.assertEqual(requests[0]['path'], '/api/chat')
        self.assertEqual(requests[0]['payload']['format'], 'json')
        self.assertFalse(requests[0]['payload']['stream'])
        self.assertEqual(requests[0]['payload']['options']['num_predict'], 1024)

    def test_real_openai_wire_contract_key_only_in_header(self):
        with provider_server(lambda h, p: respond_json(h, completion())) as (origin, requests):
            result = ChatProvider(self.config(origin, 'openai-compatible'), SECRET).complete('Return JSON.', {'evidence': []})
        self.assertEqual(requests[0]['authorization'], 'Bearer ' + SECRET)
        self.assertNotIn(SECRET, json.dumps(requests[0]['payload']))
        self.assertNotIn('tools', requests[0]['payload'])
        self.assertEqual(requests[0]['payload']['max_completion_tokens'], 1024)
        self.assertEqual(result['usage']['total_tokens'], 260)

    def test_status_get_reads_only_the_loopback_status_route(self):
        with provider_server(lambda h, p: respond_json(h, status())) as (origin, requests):
            self.assertTrue(load_status(url=origin + '/v1/status')['ready'])
            with self.assertRaisesRegex(ExplanationError, 'invalid_status_endpoint'):
                load_status(url=origin + '/actions')
        self.assertEqual(len(requests), 1)
        self.assertIsNone(requests[0]['payload'])

    def test_remote_requires_explicit_opt_in_and_verified_https(self):
        for url in ('http://example.com/api/chat', 'https://example.com/api/chat', 'http://localhost:11434/api/chat'):
            with self.assertRaises(ExplanationError):
                validate_url(url)
        validate_url('https://example.com/api/chat', True)
        validate_url('http://[::1]:11434/api/chat')
        with self.assertRaises(ExplanationError):
            validate_url('http://example.com/api/chat', True)

    def test_endpoint_credentials_queries_fragments_and_non_http_schemes_rejected(self):
        for url in ('file:///tmp/key', 'http://user:pass@127.0.0.1/api/chat',
                    'http://127.0.0.1/api/chat?key=secret', 'http://127.0.0.1/api/chat#secret',
                    'http://127.0.0.1:0/api/chat', 'http://127.0.0.1/\napi/chat'):
            with self.assertRaises(ExplanationError):
                validate_url(url)

    def test_redirects_are_not_followed_and_error_bodies_are_not_returned(self):
        def redirect(handler, payload):
            handler.send_response(302)
            handler.send_header('Location', '/steal-key')
            handler.end_headers()
            handler.wfile.write(SECRET.encode())
        with provider_server(redirect) as (origin, requests):
            with self.assertRaisesRegex(ExplanationError, '^provider_http_error$'):
                request_json(origin + '/api/chat', payload={}, token=SECRET)
        self.assertEqual(len(requests), 1)

    def test_provider_error_has_no_retry_and_does_not_expose_secret_body(self):
        with provider_server(lambda h, p: respond_json(h, {'error': SECRET}, 429)) as (origin, requests):
            with self.assertRaisesRegex(ExplanationError, '^provider_http_error$'):
                ChatProvider(self.config(origin), SECRET).complete('JSON', {})
        self.assertEqual(len(requests), 1)

    def test_response_bytes_are_bounded(self):
        with provider_server(lambda h, p: respond_json(h, {'text': 'x' * MAX_RESPONSE_BYTES})) as (origin, requests):
            with self.assertRaisesRegex(ExplanationError, 'response_too_large'):
                request_json(origin + '/api/chat', payload={})

    def test_response_headers_are_bounded(self):
        def oversized(handler, payload):
            handler.send_response(200)
            handler.send_header('X-Context', 'x' * 20000)
            handler.end_headers()
        with provider_server(oversized) as (origin, requests):
            with self.assertRaisesRegex(ExplanationError, 'response_too_large'):
                request_json(origin + '/api/chat', payload={})

    def test_slow_response_obeys_total_transfer_deadline(self):
        def slow(handler, payload):
            handler.send_response(200)
            handler.end_headers()
            for _ in range(10):
                handler.wfile.write(b' ')
                handler.wfile.flush()
                time.sleep(.05)
        with provider_server(slow) as (origin, requests):
            started = time.monotonic()
            with self.assertRaisesRegex(ExplanationError, 'provider_timeout'):
                request_json(origin + '/api/chat', payload={}, timeout=.15)
            self.assertLess(time.monotonic() - started, .8)
        self.assertEqual(len(requests), 1)

    def test_truncation_refusal_tool_calls_and_invalid_content_are_rejected(self):
        invalid = []
        value = completion()
        value['choices'][0]['finish_reason'] = 'length'
        invalid.append(value)
        for field, content in [('tool_calls', [{}]), ('refusal', 'No'), ('content', None), ('content', 'not json')]:
            value = completion()
            value['choices'][0]['message'][field] = content
            invalid.append(value)
        invalid.extend([None, [], {}, {'choices': []}])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ExplanationError):
                ChatProvider(self.config(provider='openai-compatible'), transport=lambda *a, **k: value).complete('JSON', {})

    def test_ollama_incomplete_response_is_not_presented_as_success(self):
        for field, value in [('done', False), ('done_reason', 'length')]:
            response = completion(provider='ollama')
            response[field] = value
            with self.assertRaisesRegex(ExplanationError, 'incomplete_model_output'):
                ChatProvider(self.config(), transport=lambda *a, **k: response).complete('JSON', {})

    def test_provider_echoing_api_key_cannot_leak_it_in_the_result(self):
        response = completion(dict(analysis(), missing_evidence=[SECRET]))
        with self.assertRaisesRegex(ExplanationError, 'unsafe_model_output'):
            ChatProvider(self.config(provider='openai-compatible'), SECRET,
                         transport=lambda *a, **k: response).complete('JSON', {})

    def test_usage_only_contains_bounded_numeric_fields(self):
        response = completion()
        response['usage'] = dict(prompt_tokens=True, completion_tokens=-1, total_tokens=50, secret=SECRET)
        result = ChatProvider(self.config(provider='openai-compatible'), transport=lambda *a, **k: response).complete('JSON', {})
        self.assertEqual(result['usage'], {'total_tokens': 50})

    def test_config_and_secret_files_validate_without_template_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / 'key'
            key.write_text(SECRET + '\n')
            key.chmod(0o600)
            path = root / 'ai.yml'
            path.write_text(json.dumps(dict(self.config(), api_key_file='key')))
            self.assertEqual(load_provider(path)._token, SECRET)
            key.chmod(0o644)
            with self.assertRaisesRegex(ExplanationError, 'unsafe_secret_file'):
                load_provider(path)
            key.chmod(0o600)
            key.write_text('bad\nheader')
            with self.assertRaisesRegex(ExplanationError, 'invalid_api_key'):
                load_provider(path)
            path.write_text('!!python/object/apply:os.system ["touch SHOULD_NOT_EXIST"]')
            with self.assertRaises(ExplanationError):
                load_provider(path)
            self.assertFalse((root / 'SHOULD_NOT_EXIST').exists())

    def test_invalid_settings_fail_before_network(self):
        changes = [dict(provider=[]), dict(model='x\n'), dict(model='é'), dict(timeout=True),
                   dict(timeout=121), dict(max_output_tokens=100000), dict(api_key=SECRET),
                   dict(endpoint='https://example.com/api/chat'), dict(allow_remote='true')]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ExplanationError):
                ChatProvider(dict(self.config(), **change))

    def test_direct_adapter_rejects_header_injection_and_wrong_message_role(self):
        for token in ('short', 'valid-token\r\nInjected: true', 'é' * 10, 42):
            with self.subTest(token=token), self.assertRaisesRegex(ExplanationError, 'invalid_api_key'):
                ChatProvider(self.config(), token=token)
        response = completion()
        response['choices'][0]['message']['role'] = 'user'
        with self.assertRaisesRegex(ExplanationError, 'invalid_model_output'):
            ChatProvider(self.config(provider='openai-compatible'),
                         transport=lambda *args, **kwargs: response).complete('prompt', {})

    def test_local_file_size_type_and_json_recursion_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input'
            path.write_bytes(b'x' * 20)
            with self.assertRaisesRegex(ExplanationError, 'input_too_large'):
                read_file(path, 10)
            path.unlink()
            os.mkfifo(path)
            with self.assertRaisesRegex(ExplanationError, 'invalid_input_file'):
                read_file(path)
        for data in ('{"a":1,"a":2}', '{"a":NaN}', '[' * 2000 + ']' * 2000):
            with self.assertRaisesRegex(ExplanationError, 'invalid_json'):
                decode_json(data)
