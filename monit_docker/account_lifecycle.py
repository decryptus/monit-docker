"""Self-service account policy over the existing transactional auth service.

Profile and recovery metadata belong to Monit-Docker; hashing, admission limits,
session verification and private persistence remain provided by HTTPdis.
"""
import hashlib
import hmac
import re
import secrets

from httpdis.authentication import AuthenticationDenied
from monit_docker.auth_policy import username

LANGUAGES = frozenset(('system', 'en', 'fr'))
PROFILE_FIELDS = frozenset(('display_name', 'language'))
RECOVERY_TTL = 900
RECOVERY_RE = re.compile(r'[A-Za-z0-9_-]{43}')
MAX_PASSWORD_BYTES = 1024
MIN_PASSWORD_LENGTH = 12


def password_value(value):
    if (not isinstance(value, str) or len(value) < MIN_PASSWORD_LENGTH or
            len(value.encode('utf-8')) > MAX_PASSWORD_BYTES or '\x00' in value):
        raise ValueError('invalid new password')
    return value


class AccountLifecycle:
    def __init__(self, accounts):
        self.accounts = accounts
        self.store = accounts.store
        self.service = accounts.service

    def profile(self, principal):
        with self.store.transaction() as tx:
            account = tx.get('accounts', username(principal))
            if not account or not account['enabled']:
                raise AuthenticationDenied()
            return {'display_name': account.get('display_name', principal),
                    'language': account.get('language', 'system')}

    def update(self, principal, data):
        if not isinstance(data, dict) or set(data) != PROFILE_FIELDS:
            raise ValueError('invalid profile')
        name, language = data['display_name'], data['language']
        if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or
                any(ord(c) < 32 or ord(c) == 127 for c in name) or
                not isinstance(language, str) or language not in LANGUAGES):
            raise ValueError('invalid profile')
        with self.store.transaction() as tx:
            account = tx.get('accounts', username(principal))
            if not account or not account['enabled']:
                raise AuthenticationDenied()
            account.update(display_name=name.strip(), language=language)
            tx.put('accounts', principal, account)
        return self.profile(principal)

    def issue_recovery(self, principal):
        """Trusted offline administrator operation; return the secret once."""
        secret = secrets.token_urlsafe(32)
        with self.store.transaction() as tx:
            account = tx.get('accounts', username(principal))
            if not account or not account['enabled']:
                raise AuthenticationDenied()
            account['recovery'] = {'digest': hashlib.sha256(secret.encode()).hexdigest(),
                                   'expires': self.service.clock() + RECOVERY_TTL}
            tx.put('accounts', principal, account)
        return secret

    def discard_recovery(self, principal):
        with self.store.transaction() as tx:
            account = tx.get('accounts', username(principal))
            if account:
                account.pop('recovery', None)
                tx.put('accounts', principal, account)

    def _replace_password(self, tx, principal, account, encoded):
        account.update(hash=encoded, revision=secrets.token_urlsafe(32))
        account.pop('recovery', None)
        account.pop('mobile_devices', None)
        tx.put('accounts', principal, account)
        for table in ('sessions', 'tokens'):
            for key, record in tx.items(table):
                if record['principal'] == principal:
                    tx.delete(table, key)

    def change_password(self, principal, current, new, peer):
        password_value(new)
        # Reauthentication reuses HTTPdis's durable limits and bounded hash work.
        with self.store.transaction() as tx:
            before = tx.get('accounts', username(principal))
        grant = self.accounts.login(principal, current, peer)
        try:
            encoded = self.accounts.passwords.hash(new)
            with self.store.transaction() as tx:
                account = tx.get('accounts', principal)
                if not account or not before or account['revision'] != before['revision'] or not account['enabled']:
                    raise AuthenticationDenied()
                self._replace_password(tx, principal, account, encoded)
        finally:
            self.service.logout(grant.secret)

    def recover(self, principal, secret, new, peer):
        username(principal)
        password_value(new)
        # Reuse the same limits as login, including across daemon restarts.
        self.service._admit_login(principal, peer)
        if not isinstance(secret, str) or RECOVERY_RE.fullmatch(secret) is None:
            raise AuthenticationDenied()
        with self.store.transaction() as tx:
            before = tx.get('accounts', principal)
        recovery = (before or {}).get('recovery', {})
        digest = hashlib.sha256(secret.encode()).hexdigest()
        if (not before or not before['enabled'] or recovery.get('expires', 0) <= self.service.clock()
                or not hmac.compare_digest(recovery.get('digest', ''), digest)):
            raise AuthenticationDenied()
        encoded = self.accounts.passwords.hash(new)
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if account != before or recovery['expires'] <= self.service.clock():
                raise AuthenticationDenied()
            self._replace_password(tx, principal, account, encoded)
