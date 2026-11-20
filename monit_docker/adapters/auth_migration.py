"""Offline, non-destructive import of explicit legacy accounts and rights."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import tempfile
import time

from monit_docker.auth_policy import username, role_scopes
from monit_docker.adapters.local_auth import Accounts, valid_legacy_hash

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_ACCOUNTS = 4096
MANIFEST_VERSION = 1

def read_file(path, limit=MAX_SOURCE_BYTES):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            raise ValueError('migration input must be a regular file without group/other write access')
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('migration input too large')
        return data

def plan(htpasswd, roles_file):
    source, roles_data = read_file(htpasswd), read_file(roles_file)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate role assignment')
            result[key] = value
        return result
    roles = json.loads(roles_data, object_pairs_hook=unique)
    if not isinstance(roles, dict):
        raise ValueError('roles must map every username to an explicit role')
    records = {}
    for line in source.decode('utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        principal, separator, encoded = line.partition(':')
        username(principal)
        if not separator or principal in records or not valid_legacy_hash(encoded):
            raise ValueError('duplicate account or unsupported password hash; reset explicitly before migration')
        records[principal] = encoded
    if not records or len(records) > MAX_ACCOUNTS or set(records) != set(roles):
        raise ValueError('every imported account needs exactly one explicit role')
    for role in roles.values():
        role_scopes(role)
    return source, roles_data, records, roles

def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())

def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

def migrate(htpasswd, roles_file, database, backup_dir=None, *, apply=False):
    source, roles_data, records, roles = plan(htpasswd, roles_file)
    database = Path(database).absolute()
    if database.exists() or database.is_symlink():
        raise ValueError('target database already exists; it will never be merged or overwritten')
    result = {'applied': False, 'accounts': len(records), 'sessions_imported': 0,
              'roles': {role: list(roles.values()).count(role) for role in sorted(set(roles.values()))}}
    if not apply:
        return result
    if not backup_dir:
        raise ValueError('an explicit new backup directory is required')
    parent = database.parent.stat()
    if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise ValueError('target directory must be private and owned by the daemon user')
    backup = Path(backup_dir).absolute()
    backup.mkdir(mode=0o700, parents=False, exist_ok=False)
    private_write(backup / 'htpasswd.original', source)
    private_write(backup / 'roles.original.json', roles_data)
    manifest = {'version': MANIFEST_VERSION, 'database': str(database),
                'source': str(Path(htpasswd).absolute()), 'created_at': time.time(),
                'source_sha256': hashlib.sha256(source).hexdigest(),
                'roles_sha256': hashlib.sha256(roles_data).hexdigest(), 'accounts': len(records)}
    private_write(backup / 'manifest.json', json.dumps(manifest, sort_keys=True).encode())
    # Persist directory entries too: a completed backup must survive a crash
    # before publishing the new database and reporting a successful migration.
    sync_directory(backup)
    sync_directory(backup.parent)
    fd, candidate = tempfile.mkstemp(prefix='.auth-migration-', dir=str(database.parent))
    os.close(fd)
    accounts = None
    try:
        accounts = Accounts(candidate, create=True)
        with accounts.store.transaction() as tx:
            for principal, encoded in records.items():
                tx.put('accounts', principal, {'hash': encoded, 'scopes': sorted(role_scopes(roles[principal])),
                    'revision': secrets.token_urlsafe(32), 'enabled': True})
        if len(accounts.list_users()) != len(records):
            raise ValueError('imported account count differs')
        accounts.close()
        accounts = None
        connection = sqlite3.connect(candidate)
        try:
            if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('database integrity check failed')
        finally:
            connection.close()
        if read_file(htpasswd) != source or read_file(roles_file) != roles_data:
            raise ValueError('migration inputs changed during import')
        # Publish without overwriting any file created concurrently. Remove the
        # temporary hard link before a server opens the private single-link store.
        os.link(candidate, database)
        os.unlink(candidate)
        sync_directory(database.parent)
        result.update(applied=True, backup=str(backup))
        return result
    finally:
        if accounts is not None:
            accounts.close()
        if os.path.exists(candidate):
            os.unlink(candidate)

def rollback(database, backup_dir, *, writers_stopped=False):
    if not writers_stopped:
        raise ValueError('stop the authentication server and acknowledge --writers-stopped')
    database, backup = Path(database).absolute(), Path(backup_dir).absolute()
    manifest = json.loads(read_file(backup / 'manifest.json'))
    if manifest.get('version') != MANIFEST_VERSION or manifest.get('database') != str(database):
        raise ValueError('backup does not belong to this database')
    for name, key in [('htpasswd.original', 'source_sha256'), ('roles.original.json', 'roles_sha256')]:
        if hashlib.sha256(read_file(backup / name)).hexdigest() != manifest[key]:
            raise ValueError('backup verification failed')
    info = database.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_uid != os.geteuid():
        raise ValueError('invalid private database')
    from httpdis.auth_sqlite import APPLICATION_ID, SCHEMA_VERSION
    connection = sqlite3.connect(str(database))
    try:
        if (connection.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                or connection.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
            raise ValueError('database identity differs from migration target')
    finally:
        connection.close()
    # Keep all post-migration users/sessions for recovery; never delete or replace
    # the legacy source. Proxy/daemon configuration restoration is an operator step.
    destination = database.with_name(database.name + '.retired-' + secrets.token_hex(8))
    os.rename(database, destination)
    sync_directory(database.parent)
    return {'retained_database': str(destination), 'legacy_source_unchanged': True,
            'next_step': 'restore the saved legacy proxy/daemon configuration before restarting'}
