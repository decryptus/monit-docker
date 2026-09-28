"""Measured local journal/HTTP baselines, isolated per workload; no Docker actions."""
import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
from monit_docker.audit import AuditJournal, encoded, event_record, export_events
from monit_docker.audit_query import AuditQuery, AuditReader, SCAN_BYTES
from monit_docker.adapters.http import StatusServer
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.domain.rules import CycleResult
from monit_docker.service import MonitorService

_PROFILES = (1, 50)
_LINE_BYTES = 1024
_FILES = 5
_SAMPLES = 7
_HTTP_SAMPLES = 30
_BOUNDS = {'first_page_p95_ms': 2000, 'first_page_peak_python_bytes': 16 * 1024**2,
           'sparse_search_ms': 60000, 'export_ms': 60000, 'http_p95_ms': 2000}


def io_counters():
    return {key: int(value) for key, value in
            (line.split(':') for line in Path('/proc/self/io').read_text().splitlines())}


def measured(callback, samples):
    timings = []
    before, cpu = io_counters(), time.process_time()
    for _ in range(samples):
        start = time.perf_counter()
        callback()
        timings.append((time.perf_counter() - start) * 1000)
    after = io_counters()
    return dict(samples=samples, median_ms=statistics.median(timings),
                p95_ms=sorted(timings)[math.ceil(samples * .95) - 1],
                max_ms=max(timings), cpu_ms=(time.process_time()-cpu)*1000,
                io_delta={key: after[key]-before[key] for key in before})


def workload(mib):
    with tempfile.TemporaryDirectory(prefix='monit-benchmark-') as directory:
        path = Path(directory) / 'events.jsonl'
        record = event_record('action', 'completed', result='succeeded',
                              correlation_id='reference-action', reason='')
        record['reason'] = 'x' * (_LINE_BYTES-len(encoded(record)))
        per_file = mib * 1024**2 // _FILES // _LINE_BYTES
        for number in range(_FILES):
            target = path if number == 0 else Path(str(path)+'.'+str(number))
            with target.open('wb') as stream:
                for index in range(per_file):
                    record['event_id'] = '%032x' % (number * per_file + index)
                    stream.write(encoded(record))
        journal = AuditJournal(path, max_bytes=max(65536, per_file*_LINE_BYTES), files=_FILES, emit=False)
        reader = AuditReader(journal)
        def first():
            page = reader.page(AuditQuery(), 'benchmark')
            assert len(page['records']) == 100 and page['scanned_bytes'] <= SCAN_BYTES
        first()  # Explicit warm-up; no claim of cold physical disk latency.
        page_metrics = measured(first, _SAMPLES)
        tracemalloc.start()
        first()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        correlation = measured(lambda: reader.page(AuditQuery({'correlation_id': 'reference-action'}), 'benchmark'), _SAMPLES)
        def sparse():
            cursor = None
            for _ in range(mib * 4 + 10):
                page = reader.page(AuditQuery({'result': 'failed'}, cursor), 'benchmark')
                assert not page['records'] and page['scanned_bytes'] <= SCAN_BYTES
                cursor = page['next_cursor']
                if not cursor:
                    return
            raise AssertionError('pagination failed to finish')
        search = measured(sparse, 1)
        def export():
            with open(os.devnull, 'w') as stream:
                export_events(journal.read(), stream, 'jsonl')
        exports = measured(export, 1)
        # Full active file forces an actual rotation on the first append.
        rotation = measured(lambda: journal.record('action', 'completed', result='succeeded'), 1)
        assert Path(str(path)+'.1').exists()
        assert len(journal.read()) == per_file*(_FILES-1)+1
        result = CycleResult(tuple(ContainerSnapshot(id='%064x' % i, name='web-%s' % i,
                             status='running', cpu_percent=1, mem_usage=1024) for i in range(100)), ())
        monitor = MonitorService(lambda observer: result, stale_after=300)
        monitor.run_cycle()
        server = StatusServer(('127.0.0.1', 0), monitor)
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        def http_status():
            connection = http.client.HTTPConnection(*server.server_address, timeout=3)
            try:
                connection.request('GET', '/v1/status')
                response = connection.getresponse()
                body = json.loads(response.read())
                assert response.status == 200 and len(body['containers']) == 100
            finally:
                connection.close()
        try:
            http_status()
            http_metrics = measured(http_status, _HTTP_SAMPLES)
        finally:
            server.shutdown(); thread.join(5); server.server_close()
            assert not thread.is_alive()
        values = dict(first_page_p95_ms=page_metrics['p95_ms'], first_page_peak_python_bytes=peak,
                      sparse_search_ms=search['max_ms'], export_ms=exports['max_ms'],
                      http_p95_ms=http_metrics['p95_ms'])
        return dict(requested_mib=mib, actual_bytes=per_file*_FILES*_LINE_BYTES,
                    records=per_file*_FILES, first_page=page_metrics, correlation_page=correlation,
                    sparse_search=search, full_jsonl_export=exports, rotation=rotation,
                    http_status_100_containers=http_metrics, first_page_peak_python_bytes=peak,
                    process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    bounds=_BOUNDS, breaches=[key for key in values if values[key] > _BOUNDS[key]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', type=int, choices=_PROFILES)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    if options.worker:
        print(json.dumps(workload(options.worker)))
        return
    if not options.output:
        parser.error('--output is required')
    cpu = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                if line.startswith('model name')), 'unknown')
    report = dict(python=sys.version, platform=platform.platform(), cpu=cpu,
                  logical_cpus=os.cpu_count(),
                  script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  working_tree_dirty=bool(subprocess.check_output(
                      ['git', 'status', '--porcelain'], cwd=_ROOT, text=True).strip()), commit=subprocess.check_output(
                      ['git', 'rev-parse', 'HEAD'], cwd=_ROOT, text=True).strip(), profiles=[])
    for mib in _PROFILES:
        raw = subprocess.check_output([sys.executable, __file__, '--worker', str(mib)], text=True, timeout=180)
        report['profiles'].append(json.loads(raw))
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    if any(p['breaches'] for p in report['profiles']):
        raise SystemExit('Reference workload exceeded a regression guardrail')


if __name__ == '__main__':
    main()
