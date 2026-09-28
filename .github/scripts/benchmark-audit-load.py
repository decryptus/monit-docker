"""Finite Linux loopback audit load, with isolated workers and bounded telemetry."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import gc
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from monit_docker.audit import AuditJournal, encoded, event_record
from monit_docker.audit_query import AuditReader, PAGE_LIMIT, SCAN_BYTES
from monit_docker.adapters.http import StatusServer
from monit_docker.adapters.http_security import HttpSecurity
from monit_docker.service import MonitorService

_READERS = (2, 6)
_DEFAULT_SECONDS = 15
_MAX_SECONDS = 3600
_TIMEOUT = 10
_FILE_BYTES = 256 * 1024
_FILES = 5
_RECORD_BYTES = 1024
_WRITER_PAUSE = .002
_SAMPLE_INTERVAL = 1
_HISTOGRAM_MS = 10000
_PATH = '/v1/audit'
_TOKEN = 'b' * 64  # Disposable local fixture only.
_HEADERS = {'X-Monit-Audit-Token': _TOKEN, 'X-Monit-Actor': 'load-test'}


class Timings:
    """Fixed-size millisecond histogram, independent of the request count."""
    def __init__(self):
        self.bins = [0] * (_HISTOGRAM_MS + 1)
        self.count = 0
        self.maximum = 0

    def add(self, seconds):
        ms = seconds * 1000
        self.count += 1
        self.maximum = max(self.maximum, ms)
        self.bins[min(_HISTOGRAM_MS, math.ceil(ms))] += 1

    def report(self):
        cumulative = 0
        p95 = None
        for ms, count in enumerate(self.bins):
            cumulative += count
            if self.count and cumulative >= math.ceil(self.count * .95):
                p95 = ms
                break
        return dict(count=self.count, p95_bucket_ms=p95, max_ms=self.maximum,
                    overflow_bucket_count=self.bins[-1])


def resources():
    rss = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
               if line.startswith('VmRSS:'))
    return dict(rss_kib=int(rss), open_fds=len(list(Path('/proc/self/fd').iterdir())))


def counters():
    return {key: int(value) for key, value in
            (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}


def workload(readers, seconds):
    with tempfile.TemporaryDirectory(prefix='monit-load-') as directory:
        path = Path(directory) / 'audit.jsonl'
        fixture = event_record('action', 'completed', reason='')
        fixture['reason'] = 'x' * (_RECORD_BYTES - len(encoded(fixture)))
        for number in range(_FILES):
            target = path if not number else Path(str(path) + '.' + str(number))
            with target.open('wb') as stream:
                for index in range(_FILE_BYTES // _RECORD_BYTES):
                    fixture['event_id'] = '%032x' % (number * 256 + index)
                    stream.write(encoded(fixture))
        journal = AuditJournal(path, max_bytes=_FILE_BYTES, files=_FILES, emit=False)
        reader = AuditReader(journal)
        before = resources()
        monitor = MonitorService(lambda observer: None, audit_reader=reader)
        server = StatusServer(('127.0.0.1', 0), monitor, HttpSecurity(audit_token=_TOKEN))
        server_thread = threading.Thread(target=server.serve_forever)
        server_thread.start()

        def request():
            connection = http.client.HTTPConnection(*server.server_address, timeout=_TIMEOUT)
            try:
                connection.request('GET', _PATH, headers=_HEADERS)
                response = connection.getresponse()
                body = json.loads(response.read())
                if response.status == 503:
                    assert body == {'error': 'audit_busy'}, body
                    return 'busy'
                assert response.status == 200, response.status
                rows = body['records']
                assert rows and len(rows) <= PAGE_LIMIT and body['scanned_bytes'] <= SCAN_BYTES
                assert len({row['event_id'] for row in rows}) == len(rows)
                return 'ok'
            finally:
                connection.close()

        stop = threading.Event()
        barrier = threading.Barrier(readers + 2, timeout=_TIMEOUT)
        def read_loop():
            timings = {'ok': Timings(), 'busy': Timings()}
            barrier.wait()
            while not stop.is_set():
                start = time.monotonic()
                outcome = request()
                timings[outcome].add(time.monotonic() - start)
            return {key: value.report() for key, value in timings.items()}

        def write_loop():
            timings = Timings()
            last = None
            barrier.wait()
            while not stop.is_set():
                start = time.monotonic()
                last = journal.record('action', 'completed', reason=fixture['reason'],
                                      correlation_id='sustained-writer')
                timings.add(time.monotonic() - start)
                stop.wait(_WRITER_PAUSE)
            return timings.report(), last

        try:
            assert request() == 'ok'
            with ThreadPoolExecutor(max_workers=readers + 1) as pool:
                futures = [pool.submit(read_loop) for _ in range(readers)]
                writer = pool.submit(write_loop)
                io_before, cpu_before = counters(), time.process_time()
                start = time.monotonic()
                samples = []
                try:
                    barrier.wait()
                    deadline = time.monotonic() + seconds
                    while time.monotonic() < deadline:
                        samples.append(dict(elapsed_seconds=time.monotonic()-start, **resources()))
                        for future in futures + [writer]:
                            if future.done():
                                future.result()  # Fail immediately on worker errors.
                                raise AssertionError('worker stopped before deadline')
                        stop.wait(min(_SAMPLE_INTERVAL, max(0, deadline-time.monotonic())))
                finally:
                    stop.set()
                read_results = [future.result(timeout=_TIMEOUT + 1) for future in futures]
                write_result, last = writer.result(timeout=_TIMEOUT + 1)
            elapsed = time.monotonic() - start
            cpu_seconds = time.process_time() - cpu_before
            io_after = counters()
            assert last is not None and write_result['count'] > 0
            assert all(result['ok']['count'] > 0 for result in read_results)
            assert request() == 'ok'  # Recovery after the competing workers finish.
            with journal.iter_records() as records:
                retained = list(records)
            assert retained[-1]['event_id'] == last['event_id']
            assert len({row['event_id'] for row in retained}) == len(retained)
            retained_bytes = sum(p.stat().st_size for p in Path(directory).glob('audit.jsonl*')
                                 if p.name != 'audit.jsonl.lock')
            assert retained_bytes <= _FILES * (_FILE_BYTES + len(encoded(last)))
        finally:
            stop.set()
            server.shutdown(); server_thread.join(_TIMEOUT); server.server_close()
            assert not server_thread.is_alive()
        gc.collect()
        after = resources()
        assert after['open_fds'] == before['open_fds'], (before, after)
        return dict(readers=readers, requested_seconds=seconds, elapsed_seconds=elapsed,
                    writer=write_result, reader_results=read_results, resource_samples=samples,
                    resources_before=before, resources_after=after, process_cpu_seconds=cpu_seconds,
                    io_delta={key: io_after[key]-io_before[key] for key in io_before},
                    retained_bytes=retained_bytes, recovery_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=int, default=_DEFAULT_SECONDS)
    parser.add_argument('--worker', type=int, choices=_READERS)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 1 <= args.seconds <= _MAX_SECONDS:
        parser.error('--seconds must be between 1 and 3600')
    if args.worker:
        print(json.dumps(workload(args.worker, args.seconds)))
        return
    if args.output is None:
        parser.error('--output is required')
    report = dict(python=sys.version, platform=platform.platform(), logical_cpus=os.cpu_count(),
                  cpu=next(line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                           if line.startswith('model name')),
                  commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=_ROOT, text=True).strip(),
                  working_tree_dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=_ROOT, text=True).strip()),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), profiles=[])
    for readers in _READERS:
        raw = subprocess.check_output([sys.executable, __file__, '--worker', str(readers),
                                       '--seconds', str(args.seconds)], timeout=args.seconds + 60, text=True)
        report['profiles'].append(json.loads(raw))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
