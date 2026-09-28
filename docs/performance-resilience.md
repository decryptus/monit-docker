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
  each. The export validates and streams the full journal to `/dev/null`.
- HTTP: 30 sequential new loopback connections to `/v1/status`, 100 cached
  container snapshots, response read and JSON parsing included. This exercises
  HTTPdis but excludes Docker sampling, TLS/proxies, remote network and browser
  rendering. It is not a UI page-load benchmark.
- Authenticated audit HTTP: 30 requests each for first page, correlation filter,
  missing-result page, and exact-page JSONL/CSV exports. Export event IDs must match
  the displayed filtered page, including order. A sparse HTTP request scans one
  bounded page, unlike the direct full-history sparse search above.
- Concurrent load: two HTTP readers make ten requests each while a durable writer
  appends up to 1,000 events, stopping when both readers finish. A shared barrier
  starts all three workers. Reports count successful reads and `503 audit_busy`
  separately. The post-0.0.80 candidate retries writer-lock acquisition for up to
  100 ms, using nonblocking attempts with sleeps of at most 2 ms. The two-reader
  admission limit still rejects excess readers immediately.
  Concurrent latency includes both response types and must not be interpreted as
  successful-read latency alone. A final filtered read must succeed and find the
  writer's events. Writer timing includes durable append; this short burst is not
  a saturation, throughput or long-running fairness test.
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

The baseline above predates streaming. Full CLI export now validates then streams
from fixed-size open-file snapshots. In the same local 50 MiB workload, process
peak RSS fell from about 252 MiB to 35 MiB; export wall time was about 2.9 seconds
(previous retained run: 3.0 seconds). These single samples establish the memory
gain, not a reliable speedup. The [streaming report](benchmarks/2026-09-28-streaming.json)
retains all measurements. The post-export rotation count check also now streams,
so it does not mask export improvements with a separate materialized read.
`AuditJournal.read()` and `audit-send` still materialize data; no whole-agent
memory commitment follows from this CLI export improvement.

The [authenticated HTTP report](benchmarks/2026-09-28-http.json) adds the next
local candidate. On the 50 MiB profile, sequential p95 was about 9.5 ms for the
first page, 10.1 ms for correlation filtering, 27.2 ms for a missing-result page,
and 11.3 / 13.6 ms for JSONL / CSV page export. The concurrent burst completed
15 durable writes; 11 of 20 reads succeeded and nine returned `audit_busy`.
The final read recovered successfully. These busy responses are visible load
shedding, not successful reads; this small sample does not establish an
acceptable production rejection rate or a sustained-load capacity.

The [bounded-contention candidate report](benchmarks/2026-09-28-contention.json)
records the same workload after adding the 100 ms snapshot-lock wait budget.
Both profiles returned 20 successful concurrent reads and zero busy responses.
For 50 MiB, the two readers' p95 values were 23.4 and 23.0 ms, while 16 durable
writes completed (maximum append time 15.4 ms). These short runs suggest fewer
transient rejections; they do not establish zero rejections or writer fairness
under sustained load. Persistent contention still returns `audit_busy`, and
content scanning continues after releasing the shared lock.

## Finite sustained and saturated load

```sh
python .github/scripts/benchmark-audit-load.py --seconds 30 --output /tmp/audit-load.json
```

Each isolated process runs either two or six authenticated HTTP readers alongside
one durable writer. Five 256 KiB files start full; approximately 1 KiB new events
force rotation while reads continue. Readers use new loopback connections and
request first pages continuously. The writer sleeps 2 ms after each durable
append. CI runs 15 seconds per profile; the command above runs 30. Longer manual
runs accept up to 3,600 seconds per profile, with a parent-process timeout.

Successful and busy-response latencies have separate fixed-size millisecond
histograms. p95 is the histogram bucket's upper edge; the final bucket includes
all latencies of 10 seconds or more and its count is reported explicitly. Current
RSS and open file descriptors are sampled once per second. CPU and Linux I/O
counters cover the complete process, including the HTTP clients and measurement
threads, so they are not isolated server resource measurements. Telemetry memory
is bounded independently of the request count.

The script fails on unexpected HTTP errors, malformed/duplicate page records,
worker failures, lack of read/write progress, invalid retained history, retention
size overflow, failed post-load recovery, or file descriptors remaining open after
shutdown. It verifies the last acknowledged write is retained. It does not impose
a throughput, RSS-growth, rejection-rate or fairness target. Even six active
readers do not guarantee all query slots are occupied at every instant: actual
contention depends on scheduling, I/O and the HTTP worker pool.

The [local 30-second-per-profile report](benchmarks/2026-09-28-sustained.json)
records this candidate on the same Xeon/Python environment:

| Readers | Successful reads | Busy responses | Durable writes | Worst reader successful p95 bucket | Writer p95 bucket |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2 | 3,547 | 0 | 2,185 | 30 ms | 18 ms |
| 6 | 2,007 | 7,079 | 1,031 | 71 ms | 43 ms |

The six-reader run rejected about 78% of reads while both reads and writes kept
progressing. This is observed overload behavior, not an accepted production
rejection target. Descriptor counts returned from four to four after shutdown in
both profiles. RSS rose from roughly 33 MiB to 41 MiB during each run; that does
not demonstrate either a leak or a stable plateau. A longer run is needed to
separate initial allocation from continuing growth. Process CPU includes clients.

The [120-second-per-profile follow-up](benchmarks/2026-09-28-sustained-120s.json)
uses the same committed harness. Two readers completed 14,311 successful reads
without busy responses and 8,745 durable writes. Six readers completed 6,907
successful reads, 23,890 busy responses and 3,655 durable writes. Both runs passed
recovery, retained-history validation and descriptor cleanup (four before/after).

| Readers | Median RSS 0–30 s | 30–60 s | 60–90 s | 90–120 s | Final-window RSS range |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2 | 40,504 KiB | 41,508 KiB | 41,648 KiB | 41,732 KiB | 41,704–41,760 KiB |
| 6 | 42,790 KiB | 43,164 KiB | 43,840 KiB | 43,988 KiB | 43,924–44,052 KiB |

The final two window medians differ by 84 / 148 KiB, suggesting the initial
allocation growth is slowing. They do not prove a flat plateau or absence of a
slow leak. Successful writes had p95 buckets of 18 / 52 ms; maximum write latency
was 77 / 240 ms. The snapshot retry budget is not an end-to-end write deadline,
and overload still affects writers. Multi-hour observation remains open.

This finite run complements the short burst; it is not a multi-hour soak, an
external-client load test, or proof that every event remains available beyond
configured retention. No Docker action or production endpoint is involved.

## Regression guardrails

For these fixed workloads, CI fails if first-page p95 or cached loopback HTTP
or authenticated audit HTTP p95 exceeds 2 seconds, first-page Python peak exceeds 16 MiB, or complete sparse
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

Measure proxy/browser latency, sustained and saturated concurrent load, and
long-running CPU/RSS behavior on reference hardware.
Rehearse real read-only/full filesystems, kill points around atomic replacement,
Docker daemon restarts and delayed/unresponsive endpoints on isolated hosts.
Choose tighter operating targets from those measurements for each deployment.
The 1.0 compatibility contract does not certify these pending measurements.
