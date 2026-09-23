"""Bounded reverse journal reads; writers are locked only while opening files."""
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import stat
from threading import BoundedSemaphore
import time
from urllib.parse import parse_qs

from monit_docker.audit import AuditError, MAX_RECORD_BYTES, encoded, prepare_record

PAGE_LIMIT        = 100
SCAN_BYTES        = 1024 * 1024
PAGE_BYTES        = 512 * 1024
_BLOCK_BYTES      = 128 * 1024
_IDENTITY_BYTES   = 256
_CURSOR_TTL       = 900
_MAX_QUERY        = 32768
_MAX_CURSOR       = 24000
_FILTER_KEYS      = ('since', 'until', 'container', 'source', 'category', 'result')
_QUERY_KEYS       = frozenset(_FILTER_KEYS + ('cursor', 'format'))
_SOURCE_VALUES    = ('manual', 'automatic')
_CATEGORY_VALUES  = ('action', 'notification')
_RESULT_VALUES    = ('pending', 'succeeded', 'failed', 'rejected', 'skipped', 'simulated', 'accepted', 'received')
_EXPORT_FORMATS   = ('jsonl', 'csv')
_DATE_FILTERS     = ('since', 'until')
_FILTER_CHOICES   = {'source': _SOURCE_VALUES, 'category': _CATEGORY_VALUES, 'result': _RESULT_VALUES}


class QueryError(Exception):
    def __init__(self, code, reason):
        self.code, self.reason = code, reason
        super().__init__(reason)


def _date(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise QueryError(400, 'invalid_filters')


def parse_query(query):
    if len(query) > _MAX_QUERY:
        raise QueryError(400, 'invalid_filters')
    try:
        values = parse_qs(query, keep_blank_values=True, strict_parsing=True, max_num_fields=8)
    except ValueError:
        raise QueryError(400, 'invalid_filters')
    if set(values) - _QUERY_KEYS or any(len(value) != 1 for value in values.values()):
        raise QueryError(400, 'invalid_filters')
    values = {key: value[0] for key, value in values.items() if value[0]}
    filters = {key: values[key] for key in _FILTER_KEYS if key in values}
    if any(len(value) > 256 or not value.isprintable() for value in filters.values()):
        raise QueryError(400, 'invalid_filters')
    for key, choices in _FILTER_CHOICES.items():
        if key in filters and filters[key] not in choices:
            raise QueryError(400, 'invalid_filters')
    dates = {key: _date(filters[key]) for key in _DATE_FILTERS if key in filters}
    if 'since' in dates and 'until' in dates and dates['since'] > dates['until']:
        raise QueryError(400, 'invalid_filters')
    if values.get('format', 'jsonl') not in _EXPORT_FORMATS:
        raise QueryError(400, 'invalid_filters')
    if len(values.get('cursor', '')) > _MAX_CURSOR:
        raise QueryError(400, 'invalid_cursor')
    return filters, values.get('cursor'), values.get('format', 'jsonl')


def _matches(record, filters):
    for key, value in filters.items():
        if key == 'container':
            if value.casefold() not in (record.get('container_name') or '').casefold() and value.casefold() not in (record.get('container_id') or '').casefold():
                return False
        elif key in _DATE_FILTERS:
            stamp = _date(record['timestamp'])
            if (key == 'since' and stamp < _date(value)) or (key == 'until' and stamp > _date(value)):
                return False
        elif record.get(key) != value:
            return False
    return True


class AuditReader:
    def __init__(self, journal, token):
        self.journal = journal
        self.token   = token
        self._key    = secrets.token_bytes(32)
        self._slots  = BoundedSemaphore(2)

    def _sign(self, state):
        data = json.dumps(state, separators=(',', ':'), sort_keys=True).encode()
        value = base64.urlsafe_b64encode(data).decode().rstrip('=')
        return value + '.' + hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()

    def _decode(self, token, scope):
        try:
            value, signature = token.split('.')
            if not hmac.compare_digest(signature, hmac.new(self._key, value.encode(), hashlib.sha256).hexdigest()):
                raise ValueError()
            state = json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
            if state['scope'] != scope:
                raise ValueError()
            if state['expires'] < time.time():
                raise QueryError(410, 'cursor_expired')
            return state
        except (ValueError, KeyError, TypeError):
            raise QueryError(400, 'invalid_cursor')

    @contextmanager
    def _snapshot(self):
        descriptors = []
        lock = None
        try:
            if not self.journal.path.parent.exists():
                yield []
                return
            lock = os.open(str(self.journal.path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            if not stat.S_ISREG(os.fstat(lock).st_mode):
                raise AuditError('Invalid audit lock')
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise QueryError(503, 'audit_busy')
            paths = [self.journal.path] + [Path(str(self.journal.path) + '.' + str(n)) for n in range(1, self.journal.files)]
            snapshot = []
            for path in paths:
                try:
                    descriptor = self.journal._open(path, os.O_RDONLY)
                except FileNotFoundError:
                    continue
                descriptors.append(descriptor)
                info = os.fstat(descriptor)
                if info.st_size:
                    snapshot.append((info.st_dev, info.st_ino, info.st_size, descriptor))
            # All content reads and serialization happen after releasing the writer lock.
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
            lock = None
            yield snapshot
        finally:
            if lock is not None:
                os.close(lock)
            for descriptor in descriptors:
                os.close(descriptor)

    def page(self, query, actor):
        filters, cursor, format = parse_query(query)
        scope = hashlib.sha256(json.dumps([actor, filters], sort_keys=True).encode()).hexdigest()
        state = self._decode(cursor, scope) if cursor else None
        if not self._slots.acquire(False):
            raise QueryError(503, 'audit_busy')
        try:
            with self._snapshot() as snapshot:
                identity_reads = 0
                if state is None:
                    identities = []
                    for device, inode, size, descriptor in snapshot:
                        prefix = os.pread(descriptor, min(size, _IDENTITY_BYTES), 0)
                        identity_reads += len(prefix)
                        identities.append([device, inode, size, hashlib.sha256(prefix).hexdigest()])
                    state = dict(files=identities, index=0,
                                 offset=snapshot[0][2] if snapshot else 0,
                                 expires=time.time() + _CURSOR_TTL, scope=scope)
                files = {(item[0], item[1]): item for item in snapshot}
                for device, inode, size, marker in state['files'][state['index']:]:
                    current = files.get((device, inode))
                    if current is None or current[2] < size:
                        raise QueryError(410, 'cursor_expired')
                    prefix = os.pread(current[3], min(size, _IDENTITY_BYTES), 0)
                    identity_reads += len(prefix)
                    if hashlib.sha256(prefix).hexdigest() != marker:
                        raise QueryError(410, 'cursor_expired')
                page_cursor = self._sign(state)
                records, scanned, output_bytes = [], identity_reads, 0
                stop = False
                while state['index'] < len(state['files']) and not stop:
                    device, inode, end, marker = state['files'][state['index']]
                    descriptor = files[(device, inode)][3]
                    offset = state['offset']
                    if not offset:
                        state['index'] += 1
                        if state['index'] < len(state['files']):
                            state['offset'] = state['files'][state['index']][2]
                        continue
                    if SCAN_BYTES - scanned < _BLOCK_BYTES:
                        break
                    start = max(0, offset - _BLOCK_BYTES)
                    block = os.pread(descriptor, offset - start, start)
                    scanned += len(block)
                    if len(block) != offset - start or not block.endswith(b'\n'):
                        raise AuditError('Incomplete audit record')
                    lines = block.split(b'\n')[:-1]
                    if start:
                        lines = lines[1:]  # boundary fragment is read again in the next block
                    if not lines:
                        raise AuditError('Oversized audit record')
                    for line in reversed(lines):
                        if not line or len(line) + 1 > MAX_RECORD_BYTES:
                            raise AuditError('Invalid audit record')
                        record = prepare_record(json.loads(line))
                        if _matches(record, filters):
                            length = len(encoded(record))
                            if len(records) >= PAGE_LIMIT or output_bytes + length > PAGE_BYTES:
                                stop = True
                                break
                            records.append(record)
                            output_bytes += length
                        state['offset'] -= len(line) + 1
                        if len(records) == PAGE_LIMIT:
                            stop = True
                            break
                # Empty files at the end do not need another request.
                while state['index'] < len(state['files']) and not state['offset']:
                    state['index'] += 1
                    if state['index'] < len(state['files']):
                        state['offset'] = state['files'][state['index']][2]
                more = state['index'] < len(state['files'])
                return dict(records=records, next_cursor=self._sign(state) if more else None,
                            page_cursor=page_cursor, scanned_bytes=scanned, limit=PAGE_LIMIT), format
        except QueryError:
            raise
        except (OSError, ValueError, TypeError, KeyError, AuditError):
            raise QueryError(503, 'audit_unavailable')
        finally:
            self._slots.release()
