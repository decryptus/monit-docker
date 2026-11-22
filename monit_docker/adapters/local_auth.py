"""Monit-Docker account policy over HTTPdis's durable authentication service."""
import base64
import re
import secrets
from pathlib import Path

from httpdis.auth_backend import LocalAuthService, Argon2Passwords
from httpdis.auth_sqlite import SQLiteAuthStore
from httpdis.authentication import AuthenticationDenied
from monit_docker.auth_policy import username, email_identifier, role_scopes, role_name

LEGACY_CRYPT_RE = re.compile(r'\$(?:5|6)\$(?:rounds=([0-9]{4,7})\$)?[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{43,86}')
LEGACY_BCRYPT_RE = re.compile(r'\$2[aby]\$(0[4-9]|1[0-4])\$[./0-9A-Za-z]{53}')
SESSION_ID_RE = re.compile(r'[0-9a-f]{64}')

def valid_legacy_hash(value):
    if not isinstance(value, str):
        return False
    match = LEGACY_CRYPT_RE.fullmatch(value)
    if match:
        expected = 43 if value.startswith('$5$') else 86
        return len(value.rsplit('$', 1)[1]) == expected and (not match[1] or 1000 <= int(match[1]) <= 1000000)
    return LEGACY_BCRYPT_RE.fullmatch(value) is not None

class MigratingPasswords:
    """Reuse HTTPdis's legacy verifier only for explicitly imported strong hashes."""
    def __init__(self):
        self.modern = Argon2Passwords()

    def hash(self, password):
        return self.modern.hash(password)

    def verify(self, encoded, password):
        if encoded.startswith('$argon2id$'):
            return self.modern.verify(encoded, password)
        if not valid_legacy_hash(encoded) or not isinstance(password, str):
            return False
        if encoded.startswith('$2') and len(password.encode('utf-8')) > 72:
            return False
        from httpdis.httpdis import HttpAuthentication
        verifier = HttpAuthentication(None)
        verifier.users = {'imported': encoded}
        auth = 'Basic ' + base64.b64encode(('imported:' + password).encode()).decode('ascii')
        return verifier.valid_authorization(auth)

class Accounts:
    def __init__(self, filename, *, create=False, clock=None, passwords=None):
        path = Path(filename)
        if not create and not path.is_file():
            raise ValueError('authentication database must be provisioned before serving')
        self.store = SQLiteAuthStore(str(path))
        try:
            self.passwords = passwords or MigratingPasswords()
            options = {} if clock is None else {'clock': clock}
            self.service = LocalAuthService(self.store, self.passwords, **options)
        except Exception:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    def provision(self, principal, password, role):
        principal = username(principal)
        if not email_identifier(principal):
            # Existing accounts retain administrative password/role reset. The
            # explicit offline importer owns legacy account creation separately.
            with self.store.transaction() as tx:
                if tx.get('accounts', principal) is None:
                    raise ValueError('new account identifier must be an email address')
        self.service.provision(principal, password, role_scopes(role))

    def list_users(self):
        with self.store.transaction() as tx:
            return [{'username': key, 'role': role_name(value['scopes']), 'enabled': value['enabled']}
                    for key, value in tx.items('accounts')]

    def set_role(self, principal, role):
        scopes = role_scopes(role)
        with self.store.transaction() as tx:
            record = tx.get('accounts', username(principal))
            if record is None:
                raise ValueError('unknown account')
            record.update(scopes=sorted(scopes), revision=secrets.token_urlsafe(32))
            record.pop('recovery', None)
            record.pop('mobile_devices', None)
            tx.put('accounts', principal, record)
            for table in ('sessions', 'tokens'):
                for key, item in tx.items(table):
                    if item['principal'] == principal:
                        tx.delete(table, key)

    def login(self, principal, password, peer):
        username(principal)
        grant = self.service.login(principal, password, peer)
        # Upgrade imported hashes after verification, without changing rights or
        # invalidating the just-created session. Never overwrite a concurrent reset.
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
        if account and valid_legacy_hash(account['hash']) and len(password) >= 12:
            encoded = self.passwords.hash(password)
            with self.store.transaction() as tx:
                current = tx.get('accounts', principal)
                if current == account:
                    current['hash'] = encoded
                    tx.put('accounts', principal, current)
        return grant

    def sessions(self, principal):
        now = self.service.clock()
        with self.store.transaction() as tx:
            return [{'id': key, 'expires_at': value['expires'], 'idle_expires_at': value['idle']}
                    for key, value in tx.items('sessions') if value['principal'] == principal
                    and value['expires'] > now and value['idle'] > now]

    def revoke(self, principal, credential_id, *, administrator=False):
        if not isinstance(credential_id, str) or not SESSION_ID_RE.fullmatch(credential_id):
            raise ValueError('invalid credential identifier')
        with self.store.transaction() as tx:
            record = tx.get('sessions', credential_id)
            if record is None or (record['principal'] != principal and not administrator):
                raise AuthenticationDenied()
            tx.delete('sessions', credential_id)
