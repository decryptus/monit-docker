"""Opt-in Expo transport and bounded device registrations over private SQLite."""
import hashlib
import json
import re
import time
from pathlib import Path

import requests
from httpdis.authentication import AuthenticationDenied

EXPO_URL = 'https://exp.host/--/api/v2/push/'
EXPO_TOKEN_RE = re.compile(r'(?:Exponent|Expo)PushToken\[[A-Za-z0-9_-]{10,200}\]')
CHANNEL_RE = re.compile(r'monit-observations-[01]-[01]')
DEVICE_FIELDS = frozenset(('token', 'language', 'channel'))
DEVICE_LIMIT = 5
LEASE_SECONDS = 7 * 86400
RESPONSE_LIMIT = 65536
REQUEST_TIMEOUT = (3, 5)
MESSAGES = json.loads(Path(__file__).with_name('mobile_push_messages.json').read_text(encoding='utf-8'))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class PushDevices:
    def __init__(self, accounts):
        self.accounts = accounts
        self.store = accounts.store

    def register(self, principal, session_secret, data):
        if (not isinstance(data, dict) or set(data) != DEVICE_FIELDS or
                not isinstance(data['token'], str) or not EXPO_TOKEN_RE.fullmatch(data['token']) or
                data['language'] not in MESSAGES or not isinstance(data['channel'], str) or
                not CHANNEL_RE.fullmatch(data['channel'])):
            raise ValueError('invalid device registration')
        key, now = digest(data['token']), self.accounts.service.clock()
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if not account or not account['enabled'] or 'status:read' not in account['scopes']:
                raise AuthenticationDenied()
            devices = {k: v for k, v in account.get('mobile_devices', {}).items() if v['expires'] > now}
            if key not in devices and len(devices) >= DEVICE_LIMIT:
                raise ValueError('device registration limit')
            # One physical token belongs to only one account on this installation.
            for other, record in tx.items('accounts'):
                if other != principal and key in record.get('mobile_devices', {}):
                    record['mobile_devices'].pop(key)
                    tx.put('accounts', other, record)
            devices[key] = dict(data, session=digest(session_secret), revision=account['revision'],
                                expires=now + LEASE_SECONDS, episode=None, next_attempt=0,
                                ticket=None, receipt_at=0, delivery='registered')
            account['mobile_devices'] = devices
            tx.put('accounts', principal, account)
        return {'registered': True, 'expires_at': now + LEASE_SECONDS}

    def revoke_session(self, principal, session_id):
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if account:
                account['mobile_devices'] = {k: v for k, v in account.get('mobile_devices', {}).items() if v['session'] != session_id}
                tx.put('accounts', principal, account)

    def pending(self):
        now = self.accounts.service.clock()
        with self.store.transaction() as tx:
            return [(principal, key, device) for principal, account in tx.items('accounts')
                    if account['enabled'] and 'status:read' in account['scopes']
                    for key, device in account.get('mobile_devices', {}).items()
                    if device['revision'] == account['revision'] and device['expires'] > now]

    def update(self, principal, key, before, **changes):
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if not account or account.get('mobile_devices', {}).get(key) != before:
                return False
            account['mobile_devices'][key].update(changes)
            tx.put('accounts', principal, account)
            return True

    def remove(self, principal, key, before):
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if account and account.get('mobile_devices', {}).get(key) == before:
                account['mobile_devices'].pop(key)
                tx.put('accounts', principal, account)

    def status(self, principal, session_secret):
        session_id = digest(session_secret)
        return {'devices': [{'expires_at': d['expires'], 'delivery': d['delivery']}
                            for owner, _, d in self.pending() if owner == principal and d['session'] == session_id]}


class ExpoSender:
    def __init__(self, request=None):
        self.request = request or requests.post

    def _post(self, operation, payload):
        # Fixed HTTPS destination, no redirects, bounded response and no secret logs.
        with self.request(EXPO_URL + operation, json=payload, timeout=REQUEST_TIMEOUT,
                          allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ValueError('push provider unavailable')
            body = bytearray()
            for chunk in response.iter_content(4096):
                body.extend(chunk)
                if len(body) > RESPONSE_LIMIT:
                    raise ValueError('push provider response too large')
            return json.loads(body)['data']

    def send(self, device, origin):
        message = MESSAGES[device['language']]
        return self._post('send', {'to': device['token'], 'title': 'Monit-Docker',
            'body': message, 'data': {'screen': 'alerts', 'origin': origin},
            'ttl': 300, 'channelId': device['channel'],
            'sound': 'default' if device['channel'].split('-')[-2] == '1' else None})

    def receipt(self, ticket):
        return self._post('getReceipts', {'ids': [ticket]}).get(ticket)
