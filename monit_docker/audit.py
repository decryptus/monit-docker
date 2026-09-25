"""Versioned event journal, bounded local retention and portable exports.

Standard library only so notification adapters can use the same event format.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import csv
import fcntl
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import socket
import stat
import sys
import time
import uuid

from monit_docker.domain.errors import MonitoringError

LOG = logging.getLogger('monit-docker.audit')
MAX_RECORD_BYTES = 65536
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_SCHEMA_VERSION = 2
_FORMULA_PREFIXES = ('=', '+', '-', '@', '\uff1d', '\uff0b', '\uff0d', '\uff20')
_TEXT_ESCAPE = re.compile(r'\\(?:\\|u[0-9a-f]{4}|U[0-9a-f]{8})?')
_FORMULA_START = re.compile(r'\A( *)([%s])' % re.escape(''.join(_FORMULA_PREFIXES)))
_ASCII_TO_ESCAPE = re.compile(r'[\\\x00-\x1f\x7f]+')
_NON_ASCII = re.compile(r'[^\x00-\x7f]+')
_ASCII_ESCAPES = {code: '\\u%04x' % code for code in (*range(32), 127)}
_ASCII_ESCAPES[ord('\\')] = '\\\\'
FIELDS = ('schema_version', 'event_id', 'timestamp', 'host', 'category', 'event',
          'correlation_id', 'source', 'actor', 'container_id', 'container_name',
          'action', 'command_id', 'rule_id', 'result', 'reason', 'error_code',
          'exit_code', 'duration_ms', 'channel', 'notification_id', 'alert_name',
          'alert_status', 'delivery_status')


class AuditError(MonitoringError):
    def __init__(self, message):
        super().__init__(119, message)


def fingerprint(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def _escape_unicode_match(match):
    value = match.group()
    if value.isprintable():
        return value
    return ''.join(character if character.isprintable() else
                   ('\\u%04x' if ord(character) <= 0xffff else '\\U%08x') % ord(character)
                   for character in value)


def _escape_text(value):
    """Canonical, reversible display text shared by storage and every output."""
    if '\\' not in value and value.isprintable() and not value.lstrip(' ').startswith(_FORMULA_PREFIXES):
        return value
    value = _ASCII_TO_ESCAPE.sub(lambda match: match.group().translate(_ASCII_ESCAPES), value)
    # Only unusual Unicode needs per-character checks; preserve printable runs.
    if not value.isprintable():
        value = _NON_ASCII.sub(_escape_unicode_match, value)
    return _FORMULA_START.sub(lambda match: match.group(1) + '\\u%04x' % ord(match.group(2)), value)


def _unescape_match(match):
    token = match.group()
    if token == '\\':
        raise AuditError('Invalid audit text escape')
    if token == '\\\\':
        return '\\'
    try:
        return chr(int(token[2:], 16))
    except ValueError as error:
        raise AuditError('Invalid audit text code point') from error


def _unescape_text(value):
    if '\\' not in value:
        return value
    return _TEXT_ESCAPE.sub(_unescape_match, value)


def prepare_record(record):
    """Normalize legacy events and validate v2 before any storage or output.

    V2 textual fields contain canonical visible escapes. Versioning prevents
    double escaping and preserves the distinction between a control character
    and a username that literally contains its escape notation.
    """
    if (not isinstance(record, dict) or type(record.get('schema_version')) is not int
            or record['schema_version'] not in (1, _SCHEMA_VERSION)):
        raise AuditError('Unsupported audit schema')
    result = record.copy()
    for key, value in record.items():
        if isinstance(value, str):
            if record['schema_version'] == 1:
                result[key] = _escape_text(value)
            elif _escape_text(_unescape_text(value)) != value:
                raise AuditError('Audit text is not canonically escaped')
        elif value is not None and not isinstance(value, (int, float, bool)):
            raise AuditError('Audit event fields must be scalar values')
    result['schema_version'] = _SCHEMA_VERSION
    return result


def event_record(category, event, **fields):
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError('Unknown audit fields: ' + ', '.join(sorted(unknown)))
    record = {key: None for key in FIELDS}
    record.update(fields)
    record.update(schema_version=1, event_id=uuid.uuid4().hex,
                  timestamp=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                  host=socket.gethostname(), category=category, event=event)
    return prepare_record(record)


def encoded(record):
    data = (json.dumps(prepare_record(record), ensure_ascii=True, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
    if len(data) > MAX_RECORD_BYTES:
        raise AuditError('Audit event exceeds the size limit')
    return data


class AuditJournal:
    def __init__(self, path, max_bytes=DEFAULT_MAX_BYTES, files=5, emit=True):
        if not path or not str(path).strip() or max_bytes < MAX_RECORD_BYTES or not 1 <= files <= 100:
            raise ValueError('Audit journal requires a path, at least 65536 bytes and 1..100 files')
        self.path = Path(path).expanduser().absolute()
        self.max_bytes, self.files, self.emit = max_bytes, files, emit
        self.failures = 0

    @contextmanager
    def _lock(self, create=True):
        if create:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise AuditError('Audit lock must be a regular file')
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield
        finally:
            os.close(lock)

    def _open(self, path, flags):
        descriptor = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise AuditError('Audit journal must be a regular file')
        return descriptor

    def append(self, record):
        record = prepare_record(record)
        data = encoded(record)
        try:
            with self._lock():
                descriptor = self._open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
                try:
                    os.fchmod(descriptor, 0o600)
                    size = os.fstat(descriptor).st_size
                finally:
                    os.close(descriptor)
                if size:
                    reader = self._open(self.path, os.O_RDONLY)
                    try:
                        os.lseek(reader, -1, os.SEEK_END)
                        if os.read(reader, 1) != b'\n':
                            raise AuditError('Audit journal ends with an incomplete event')
                    finally:
                        os.close(reader)
                if size and size + len(data) > self.max_bytes:
                    for number in range(self.files - 1, 0, -1):
                        previous = self.path if number == 1 else Path(str(self.path) + '.' + str(number - 1))
                        if previous.exists():
                            os.replace(previous, str(self.path) + '.' + str(number))
                    if self.files == 1:
                        self.path.unlink()
                descriptor = self._open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
                try:
                    os.fchmod(descriptor, 0o600)
                    view = memoryview(data)
                    while view:
                        written = os.write(descriptor, view)
                        if not written:
                            raise AuditError('Incomplete audit write')
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except (OSError, ValueError, AuditError) as error:
            self.failures += 1
            raise AuditError('Unable to persist audit event') from error
        if self.emit:
            # Raw JSON goes to stderr, independent of the diagnostic log level.
            try:
                sys.stderr.write(data.decode('utf-8'))
                sys.stderr.flush()
            except (OSError, ValueError):
                LOG.error('Audit event persisted but stderr forwarding failed')
        return record

    def record(self, category, event, **fields):
        return self.append(event_record(category, event, **fields))

    def finish(self, category, event, **fields):
        """Do not repeat or misreport an executed operation if its final write fails."""
        record = event_record(category, event, **fields)
        try:
            return self.append(record)
        except AuditError:
            LOG.critical('Audit completion could not be persisted; operation must not be retried automatically')
            try:
                sys.stderr.write(encoded(record).decode('utf-8'))
                sys.stderr.flush()
            except (OSError, ValueError):
                pass
            return record

    def read(self):
        """Take a bounded consistent snapshot, then release the writer lock."""
        chunks = []
        with self._lock(create=False):
            paths = [Path(str(self.path) + '.' + str(n)) for n in range(self.files - 1, 0, -1)] + [self.path]
            for path in paths:
                try:
                    descriptor = self._open(path, os.O_RDONLY)
                except FileNotFoundError:
                    continue
                with os.fdopen(descriptor, 'rb') as stream:
                    data = stream.read(self.max_bytes + MAX_RECORD_BYTES + 1)
                    if len(data) > self.max_bytes + MAX_RECORD_BYTES:
                        raise AuditError('Audit file exceeds configured retention size')
                    chunks.append(data)
        records = []
        for chunk in chunks:
            if chunk and not chunk.endswith(b'\n'):
                raise AuditError('Audit journal contains an incomplete event')
            for line in chunk.splitlines():
                try:
                    record = json.loads(line)
                    records.append(prepare_record(record))
                except (UnicodeError, ValueError) as error:
                    raise AuditError('Audit journal contains an incomplete or invalid event') from error
        return records


def export_events(records, stream, format='jsonl'):
    records = (prepare_record(record) for record in records)
    if format == 'jsonl':
        for record in records:
            stream.write(encoded(record).decode('utf-8'))
    elif format == 'csv':
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        for record in records:
            writer.writerow(record)
    else:
        raise ValueError('Unknown audit export format')


@contextmanager
def notification_delivery(journal, channel, notification_id, source='automatic', actor='notification-adapter'):
    """Record the adapter's acknowledgement; not delivery to a human recipient."""
    fields = dict(correlation_id=uuid.uuid4().hex, source=source, actor=actor,
                  channel=channel, notification_id=notification_id)
    journal.record('notification', 'started', result='pending', delivery_status='pending', **fields)
    started = time.monotonic()
    try:
        yield
    except Exception as error:
        journal.finish('notification', 'completed', result='failed', delivery_status='failed',
                       reason=type(error).__name__, duration_ms=round((time.monotonic() - started) * 1000), **fields)
        raise
    else:
        journal.finish('notification', 'completed', result='accepted', delivery_status='accepted',
                       duration_ms=round((time.monotonic() - started) * 1000), **fields)
