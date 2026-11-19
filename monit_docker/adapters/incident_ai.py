# Copyright 2019-2026 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit provider configuration and bounded, non-streaming JSON transports."""
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from monit_docker.incident import ExplanationError, json_bytes, MAX_CONTEXT_BYTES, MAX_MODEL_BYTES

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CONFIG_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
MAX_HEADER_BYTES = 16384
MAX_TOKEN_BYTES = 4096
MAX_JSON_DEPTH = 64
MAX_REQUEST_BYTES = MAX_CONTEXT_BYTES + 8000
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120
DEFAULT_OUTPUT_TOKENS = 1024
MAX_OUTPUT_TOKENS = 2048
PROVIDER_ROUTES = {'openai-compatible': '/v1/chat/completions', 'ollama': '/api/chat'}
CONFIG_FIELDS = frozenset(('provider', 'endpoint', 'model', 'api_key_file', 'allow_remote', 'timeout', 'max_output_tokens'))
MODEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,127}')
USAGE_FIELDS = ('prompt_tokens', 'completion_tokens', 'total_tokens')
DEFAULT_STATUS_URL = 'http://127.0.0.1:9808/v1/status'


def read_file(path, limit=MAX_FILE_BYTES, secret=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or secret and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
                raise ExplanationError('unsafe_secret_file' if secret else 'invalid_input_file')
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ExplanationError('input_too_large')
        return data
    except OSError:
        raise ExplanationError('input_unavailable') from None


def decode_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('non-finite value')
    try:
        result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
        pending = [(result, 0)]
        while pending:
            value, depth = pending.pop()
            if depth > MAX_JSON_DEPTH:
                raise ValueError('excessive nesting')
            if isinstance(value, dict):
                pending.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                pending.extend((child, depth + 1) for child in value)
        return result
    except (ValueError, UnicodeError, RecursionError):
        raise ExplanationError('invalid_json') from None


def validate_url(url, allow_remote=False):
    try:
        if not isinstance(url, str) or not url.isascii() or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment or parsed.port == 0):
            raise ValueError()
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
        if not local and (not allow_remote or parsed.scheme != 'https'):
            raise ExplanationError('remote_endpoint_not_allowed')
        return parsed
    except (ValueError, UnicodeError):
        raise ExplanationError('invalid_endpoint') from None


def request_json(url, *, payload=None, token=None, timeout=DEFAULT_TIMEOUT, limit=MAX_RESPONSE_BYTES):
    """libcurl total deadline, bounded bytes, no redirects/retries/proxies/netrc."""
    import pycurl
    validate_url(url, allow_remote=True)
    validate_token(token)
    if not pycurl.version_info()[4] & pycurl.VERSION_ASYNCHDNS:
        raise ExplanationError('asynchronous_dns_required')
    received = bytearray()
    header_size = 0
    too_large = False
    def write(chunk):
        nonlocal too_large
        if len(received) + len(chunk) > limit:
            too_large = True
            return 0
        received.extend(chunk)
        return len(chunk)
    def header(chunk):
        nonlocal header_size, too_large
        header_size += len(chunk)
        if header_size > MAX_HEADER_BYTES:
            too_large = True
            return 0
        return len(chunk)
    body = json_bytes(payload) if payload is not None else None
    if body is not None and len(body) > MAX_REQUEST_BYTES:
        raise ExplanationError('context_too_large')
    headers = ['Accept: application/json', 'Content-Type: application/json']
    if token:
        headers.append('Authorization: Bearer ' + token)
    with pycurl.Curl() as curl:
        curl.setopt(pycurl.URL, url)
        curl.setopt(pycurl.HTTPHEADER, headers)
        curl.setopt(pycurl.WRITEFUNCTION, write)
        curl.setopt(pycurl.HEADERFUNCTION, header)
        curl.setopt(pycurl.TIMEOUT_MS, int(timeout * 1000))
        curl.setopt(pycurl.CONNECTTIMEOUT_MS, min(5000, int(timeout * 1000)))
        curl.setopt(pycurl.NOSIGNAL, 1)
        curl.setopt(pycurl.FOLLOWLOCATION, False)
        curl.setopt(pycurl.PROXY, '')
        curl.setopt(pycurl.NETRC, pycurl.NETRC_IGNORED)
        curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
        if body is not None:
            curl.setopt(pycurl.POSTFIELDS, body)
        try:
            curl.perform()
        except pycurl.error as error:
            reason = ('response_too_large' if too_large else
                      'provider_timeout' if error.args[0] == pycurl.E_OPERATION_TIMEDOUT else 'provider_unavailable')
            raise ExplanationError(reason) from None
        if curl.getinfo(pycurl.RESPONSE_CODE) != 200:
            raise ExplanationError('provider_http_error')
    return decode_json(bytes(received))


def load_status(path=None, url=DEFAULT_STATUS_URL):
    if path is not None:
        return decode_json(read_file(path))
    parsed = validate_url(url)
    if parsed.path != '/v1/status':
        raise ExplanationError('invalid_status_endpoint')
    return request_json(url, timeout=5, limit=MAX_FILE_BYTES)


def validate_config(config):
    if not isinstance(config, dict) or set(config) - CONFIG_FIELDS:
        raise ExplanationError('invalid_provider_configuration')
    config = config.copy()
    if (not isinstance(config.get('provider'), str) or config['provider'] not in PROVIDER_ROUTES
            or type(config.get('allow_remote', False)) is not bool):
        raise ExplanationError('invalid_provider_configuration')
    parsed = validate_url(config.get('endpoint'), config.get('allow_remote', False))
    if parsed.path != PROVIDER_ROUTES[config['provider']]:
        raise ExplanationError('invalid_provider_route')
    if not isinstance(config.get('model'), str) or not MODEL_RE.fullmatch(config['model']):
        raise ExplanationError('invalid_model')
    for key, default, low, high in (
            ('timeout', DEFAULT_TIMEOUT, 1, MAX_TIMEOUT),
            ('max_output_tokens', DEFAULT_OUTPUT_TOKENS, 128, MAX_OUTPUT_TOKENS)):
        value = config.setdefault(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ExplanationError('invalid_provider_configuration')
    return config


def validate_token(token):
    if token is not None and (not isinstance(token, str) or not 8 <= len(token) <= MAX_TOKEN_BYTES
                              or any(not 33 <= ord(c) <= 126 for c in token)):
        raise ExplanationError('invalid_api_key')


def load_provider(path):
    from sonicprobe.helpers import load_yaml
    try:
        config = load_yaml(read_file(path, MAX_CONFIG_BYTES))
    except Exception:
        raise ExplanationError('invalid_provider_configuration') from None
    config = validate_config(config)
    token = None
    if 'api_key_file' in config:
        if not isinstance(config['api_key_file'], str) or not config['api_key_file'].strip():
            raise ExplanationError('invalid_secret_file')
        token_path = Path(path).absolute().parent / config['api_key_file']
        try:
            token = read_file(token_path, MAX_TOKEN_BYTES, secret=True).decode('ascii').strip()
        except UnicodeError:
            raise ExplanationError('invalid_api_key') from None
        validate_token(token)
    return ChatProvider(config, token)


class ChatProvider:
    def __init__(self, config, token=None, transport=request_json):
        validate_token(token)
        self.config, self._token, self.transport = validate_config(config), token, transport

    def complete(self, prompt, context):
        messages = [dict(role='system', content=prompt), dict(role='user', content=json_bytes(context).decode())]
        request = dict(model=self.config['model'], messages=messages, stream=False)
        if self.config['provider'] == 'ollama':
            request.update(format='json', options=dict(num_predict=self.config['max_output_tokens']))
        else:
            request.update(response_format=dict(type='json_object'), max_completion_tokens=self.config['max_output_tokens'])
        response = self.transport(self.config['endpoint'], payload=request, token=self._token, timeout=self.config['timeout'])
        try:
            if self.config['provider'] == 'ollama':
                if response.get('done') is not True or response.get('done_reason') != 'stop':
                    raise ExplanationError('incomplete_model_output')
                message = response['message']
                usage = dict(prompt_tokens=response.get('prompt_eval_count'), completion_tokens=response.get('eval_count'))
            else:
                choices = response['choices']
                if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
                    raise ExplanationError('incomplete_model_output')
                message = choices[0]['message']
                usage = response.get('usage') or {}
            if (message.get('role') != 'assistant' or message.get('tool_calls')
                    or message.get('function_call') or message.get('refusal')):
                raise ExplanationError('invalid_model_output')
            content = message['content']
            if not isinstance(content, str) or len(content.encode()) > MAX_MODEL_BYTES:
                raise ExplanationError('invalid_model_output')
            analysis = decode_json(content)
            if self._token and self._token in json.dumps(analysis, ensure_ascii=False):
                raise ExplanationError('unsafe_model_output')
            usage = {key: usage[key] for key in USAGE_FIELDS
                     if type(usage.get(key)) is int and 0 <= usage[key] <= 10 ** 9}
            return dict(analysis=analysis, usage=usage)
        except (TypeError, KeyError, IndexError, AttributeError, ValueError, RecursionError):
            raise ExplanationError('invalid_model_output') from None
