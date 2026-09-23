"""Reproducible 50 MiB journal benchmark; disposable local files, no Docker."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import tracemalloc
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monit_docker.audit import AuditJournal, encoded, event_record
from monit_docker.audit_query import AuditReader, SCAN_BYTES

FILE_BYTES = 10 * 1024 * 1024
FILE_COUNT = 5
LINE_BYTES = 1024

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / 'events.jsonl'
    record = event_record('action', 'completed', actor='benchmark', result='succeeded', source='automatic', reason='')
    record['reason'] = 'x' * (LINE_BYTES - len(encoded(record)))
    assert len(encoded(record)) == LINE_BYTES
    for index in range(FILE_COUNT):
        target = path if index == 0 else Path(str(path) + '.' + str(index))
        record['event_id'] = '%032x' % index
        target.write_bytes(encoded(record) * (FILE_BYTES // LINE_BYTES))
    journal = AuditJournal(path, max_bytes=FILE_BYTES, files=FILE_COUNT, emit=False)
    reader = AuditReader(journal, 'b' * 64)
    start = time.perf_counter()
    page, _ = reader.page('', 'benchmark')
    first_ms = (time.perf_counter() - start) * 1000
    tracemalloc.start()
    reader.page('', 'benchmark')
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert len(page['records']) == 100 and page['scanned_bytes'] <= SCAN_BYTES
    start = time.perf_counter()
    query = {'result': 'failed'}
    requests, scanned = 0, 0
    while True:
        page, _ = reader.page(urlencode(query), 'benchmark')
        requests += 1; scanned += page['scanned_bytes']
        assert not page['records'] and page['scanned_bytes'] <= SCAN_BYTES
        if not page['next_cursor']:
            break
        query['cursor'] = page['next_cursor']
    search_ms = (time.perf_counter() - start) * 1000
    # Deliberately pause content reads while a writer persists an event. The
    # writer must finish before the reader is resumed (no long journal lock).
    real_pread = os.pread
    entered, release = threading.Event(), threading.Event()
    def paused_read(*args):
        entered.set()
        if not release.wait(3):
            raise AssertionError('Reader held up the writer')
        return real_pread(*args)
    from unittest.mock import patch
    errors = []
    def browse():
        try:
            reader.page('', 'benchmark')
        except Exception as error:
            errors.append(error)
    with patch('monit_docker.audit_query.os.pread', side_effect=paused_read):
        worker = threading.Thread(target=browse)
        worker.start()
        assert entered.wait(2)
        start = time.perf_counter()
        try:
            journal.record('action', 'completed', result='succeeded')
            write_ms = (time.perf_counter() - start) * 1000
        finally:
            release.set(); worker.join(4)
        assert not worker.is_alive() and not errors, errors
    print(json.dumps(dict(journal_mib=50, first_page_ms=round(first_ms, 2),
                          first_page_python_peak_kib=round(peak/1024, 1),
                          full_sparse_search_ms=round(search_ms, 2), search_requests=requests,
                          writer_during_paused_read_ms=round(write_ms, 2)), indent=2))
