"""Offline, non-destructive conversion of retained journals to schema 2."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from monit_docker.audit import AuditError, MAX_RECORD_BYTES, encoded, prepare_record

_ARCHIVE_SUFFIX = re.compile(r'\.([1-9][0-9]*)\Z')
_MANIFEST_VERSION = 1
_TARGET_SCHEMA = 2
_BACKUP_DIRECTORY = 'backup'
_CONVERTED_DIRECTORY = 'converted'
_MANIFEST_NAME = 'manifest.json'


def _reject_constant(value):
    raise AuditError('Non-finite JSON number in audit journal')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AuditError('Duplicate JSON key in audit journal')
        result[key] = value
    return result


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def _source_lock(journal):
    descriptor = journal._open(str(journal.path) + '.lock', os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AuditError('Audit journal is busy; stop writers before migration') from error
        yield
    finally:
        os.close(descriptor)


def _source_paths(journal):
    # Do not silently omit older files after a retention setting was reduced.
    for path in journal.path.parent.iterdir():
        if path.name.startswith(journal.path.name):
            match = _ARCHIVE_SUFFIX.fullmatch(path.name[len(journal.path.name):])
            if match and int(match.group(1)) >= journal.files:
                raise AuditError('Archives exceed --audit-files; use the original retention settings')
    paths = [Path(str(journal.path) + '.' + str(n)) for n in range(journal.files - 1, 0, -1)]
    return paths + [journal.path]


def _file_identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _inspect(journal, path, backup=None, converted=None):
    source_hash, converted_hash, identity_hash = (hashlib.sha256() for _ in range(3))
    count = source_bytes = converted_bytes = 0
    schemas = {'1': 0, '2': 0}
    limit = journal.max_bytes + MAX_RECORD_BYTES
    descriptor = journal._open(path, os.O_RDONLY)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > limit:
            raise AuditError('Audit file exceeds configured retention size')
        while True:
            line = stream.readline(MAX_RECORD_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_RECORD_BYTES or not line.endswith(b'\n'):
                raise AuditError('Oversized or incomplete audit event')
            try:
                row = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
                prepared = prepare_record(row)
                data = encoded(prepared)
            except (ValueError, UnicodeError, RecursionError) as error:
                raise AuditError('Invalid audit event') from error
            count += 1
            schemas[str(row['schema_version'])] += 1
            source_bytes += len(line)
            converted_bytes += len(data)
            if source_bytes > limit or converted_bytes > limit:
                raise AuditError('Source or converted archive exceeds retention size; increase --audit-max-bytes')
            source_hash.update(line)
            converted_hash.update(data)
            identity_hash.update((json.dumps([prepared.get('event_id'), prepared.get('correlation_id')],
                                            ensure_ascii=True) + '\n').encode('utf-8'))
            if backup is not None:
                backup.write(line)
                converted.write(data)
        if _file_identity(before) != _file_identity(os.fstat(stream.fileno())):
            raise AuditError('Source changed during migration; stop all writers')
    return dict(name=path.name, records=count, schemas=schemas, source_bytes=source_bytes,
                converted_bytes=converted_bytes, source_sha256=source_hash.hexdigest(),
                converted_sha256=converted_hash.hexdigest(), identities_sha256=identity_hash.hexdigest())


@contextmanager
def _new_file(path):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        yield stream
        stream.flush()
        os.fsync(stream.fileno())


def migrate_journal(journal, output_dir=None, apply=False):
    """Validate by default; --apply writes a new bundle, never the live journal.

    A complete manifest is the commit marker. Failed or interrupted bundles are
    retained for inspection and must not be activated or reused as destinations.
    """
    if apply and not output_dir:
        raise AuditError('Migration requires a new output directory')
    destination = Path(output_dir).expanduser().absolute() if output_dir else None
    if destination is not None and destination.parent.resolve() == journal.path.parent.resolve():
        suffix = destination.name[len(journal.path.name):] if destination.name.startswith(journal.path.name) else None
        if suffix in ('', '.lock') or (suffix is not None and _ARCHIVE_SUFFIX.fullmatch(suffix)):
            raise AuditError('Output directory conflicts with journal or rotation paths')
    if destination is not None and os.path.lexists(destination):
        raise AuditError('Migration output directory must not already exist')
    try:
        with _source_lock(journal):
            paths = _source_paths(journal)
            inventory = []
            for path in paths:
                try:
                    inventory.append(_inspect(journal, path))
                except FileNotFoundError:
                    # A dangling symlink fails O_NOFOLLOW instead of being ignored.
                    continue
            if not inventory:
                raise AuditError('No retained journal files found')
            report = dict(manifest_version=_MANIFEST_VERSION, target_schema=_TARGET_SCHEMA,
                          status='dry_run', source=str(journal.path), files=inventory,
                          records=sum(item['records'] for item in inventory),
                          legacy_records=sum(item['schemas']['1'] for item in inventory),
                          audit_files=journal.files, audit_max_bytes=journal.max_bytes)
            if not apply:
                return report
            destination.mkdir(mode=0o700)
            backup_dir, converted_dir = (destination / name for name in (_BACKUP_DIRECTORY, _CONVERTED_DIRECTORY))
            backup_dir.mkdir(mode=0o700)
            converted_dir.mkdir(mode=0o700)
            for expected in inventory:
                source = journal.path.parent / expected['name']
                backup_path = backup_dir / expected['name']
                converted_path = converted_dir / expected['name']
                with _new_file(backup_path) as backup, _new_file(converted_path) as converted:
                    actual = _inspect(journal, source, backup, converted)
                if actual != expected or _inspect(journal, backup_path) != expected:
                    raise AuditError('Source or backup verification failed')
                checked = _inspect(journal, converted_path)
                if (checked['records'] != expected['records'] or checked['schemas']['1'] != 0
                        or checked['source_sha256'] != expected['converted_sha256']
                        or checked['identities_sha256'] != expected['identities_sha256']):
                    raise AuditError('Converted journal verification failed')
            _sync_directory(backup_dir)
            _sync_directory(converted_dir)
            report.update(status='complete', output_dir=str(destination),
                          completed_at=datetime.now(timezone.utc).isoformat())
            # Only this last rename makes the bundle complete for an operator.
            temporary = destination / (_MANIFEST_NAME + '.tmp')
            with _new_file(temporary) as manifest:
                manifest.write((json.dumps(report, ensure_ascii=True, indent=2) + '\n').encode('utf-8'))
            os.replace(temporary, destination / _MANIFEST_NAME)
            _sync_directory(destination)
            _sync_directory(destination.parent)
            return report
    except (OSError, ValueError) as error:
        raise AuditError('Migration failed; preserve any incomplete output and retry with a new directory') from error
