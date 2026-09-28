# Performance and resilience baseline

This first baseline measures reproducible local workloads and selected failure
paths. It does not close the full performance/resilience roadmap milestone or
certify production latency. Run on a disposable Linux machine:

```sh
python .github/scripts/benchmark-baseline.py --output /tmp/performance-baseline.json
python -m unittest discover -s tests -p test_resilience.py -v
```

The existing `benchmark-audit.py` additionally verifies that a paused journal
reader does not retain a lock blocking a writer. CI runs both benchmarks on
Python 3.12, uploads the JSON result for 30 days, and runs real Docker connection
loss/recovery alongside the existing Docker integration tests. Release reviewers
should preserve reports beyond the CI artifact retention period when needed.

## Workload and measurement boundaries

The new benchmark runs each requested **1 MiB / 50 MiB** profile in a separate
process. Five files contain unique event IDs and fixed 1024-byte records; integer
rounding means the smaller profile is slightly below 1 MiB. The JSON records the
actual bytes and event count. All records are successful actions with a shared
correlation ID, so the failed-result filter scans the full history without matches.
A full active file exercises rotation on append, with retained record counts
checked afterward.

- First-page and matching correlation queries: seven warm samples; reported p95
  is the maximum of these seven samples, not a statistically stable tail estimate.
- Missing-result search, full JSONL export and durable append/rotation: one sample
  each. The export includes full journal loading and writes to `/dev/null`.
- HTTP: 30 sequential new loopback connections to `/v1/status`, 100 cached
  container snapshots, response read and JSON parsing included. This exercises
  HTTPdis but excludes Docker sampling, TLS/proxies, remote network and browser
  rendering. It is not a UI page-load or authenticated audit-export benchmark.
- Each operation records wall time, process CPU time and Linux `/proc/self/io`
  deltas. `rchar/wchar` include buffered I/O and instrumentation; physical
  `read_bytes/write_bytes` may be zero due to cache or delayed writeback. These
  are process counters, not a device throughput test.
- First-page peak Python allocation is measured separately with `tracemalloc`.
  Process peak RSS is Linux `ru_maxrss` in KiB and includes fixture generation,
  imports and full exports. It cannot be attributed to one individual operation.

Fixture construction warms the filesystem cache. No cache flushing or host-wide
changes are performed. CPU model, platform, Python, logical CPUs, commit, dirty
working-tree flag and script hash accompany the measurements. Candidate working
copies and published release results must not be confused.

## Initial local observation

The [raw local report](benchmarks/2026-09-28-local.json) was produced on
2026-09-28 with Python 3.12.14 and an Intel Xeon Platinum 8370C. It identifies the
base commit and a dirty working tree because this benchmark and collector fix
were being prepared. Consult CI for the committed candidate's measurements.

The initial run observed approximately 4 ms for the first page, 0.55 MiB peak
Python allocations for that page, 0.85 s for a missing-result search over 50 MiB,
and 3.4 s for the full export. The large-profile process peak was approximately
253 MiB. Repeated measurements vary; the raw report contains the retained run.

**Full export still materializes the journal in memory.** Bounded page reads do
not imply bounded full-export memory. This is a measured limitation to address
before raising retention limits or making a whole-agent memory commitment.

## Regression guardrails

For these fixed workloads, CI fails if first-page p95 or cached loopback HTTP
p95 exceeds 2 seconds, first-page Python peak exceeds 16 MiB, or complete sparse
search / full export exceeds 60 seconds. These deliberately broad limits catch
major regressions on shared runners; they are not product SLOs. Every page must
also obey the existing scan-byte bound, sparse searches must terminate, rotation
must retain the expected count, and HTTP responses must contain all 100 snapshots.
No whole-process RSS or disk-throughput pass threshold is claimed yet.

## Failure and recovery checks

| Scenario | Verified behavior | Scope |
| --- | --- | --- |
| Process killed after a durable restart reservation | Lock is released; a new process sees the consumed budget and cannot reserve again | Actual subprocess kill; no Docker action |
| State fsync fails with ENOSPC before atomic replacement | Error 118; prior bytes and in-memory count preserved; a later save succeeds | Injected syscall error, not a filled physical device |
| Partial journal append followed by ENOSPC | Existing prefix remains; reads and further writes explicitly reject incomplete history | Injected write failure; no automatic repair |
| 64 identical submissions using 16 threads | One queued request and exactly one execution | Application service and fake executor; not distributed deduplication |
| Docker connection unavailable between successful cycles | Cached measurements cleared, readiness false, next healthy cycle recovers without actions | Real SDK against a nonexistent socket, then real daemon; not stopping host Docker |

For incomplete journals, stop all writers and preserve damaged bytes before any
operator-controlled restoration. The test restores a known fixture backup and
checks that appending resumes; production restoration must account for all later
events and never replay actions. A failed fsync after a successful rename can
have different durability uncertainty from the tested pre-replacement failure.

Existing tests also cover stale caches, failed actions, concurrent journal writers
and bounded queues. The new tests complement those checks rather than establish
an exactly-once guarantee across crashes: manual request deduplication is in-memory.

## Remaining work

Measure authenticated audit HTTP paths, export formats, proxy/browser latency,
concurrent read/write load and long-running CPU/RSS behavior on reference hardware.
Rehearse real read-only/full filesystems, kill points around atomic replacement,
Docker daemon restarts and delayed/unresponsive endpoints on isolated hosts.
Choose tighter operating targets from those measurements before 1.0 approval.
