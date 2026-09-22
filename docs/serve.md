# Continuous monitoring and HTTP endpoints

`serve` uses the same monitoring engine as `monit` and `cron`. It runs an
immediate cycle, then waits 30 seconds after each completed cycle by default.
Cycles are sequential, including when collection takes longer than the interval.
It adds no runtime dependencies. The HTTP interface only reads an in-memory cache;
a request never collects Docker statistics or executes a remediation action.

## Start a local monitor

```sh
monit-docker --name 'web*' serve --interval 30
curl http://127.0.0.1:9808/healthz
curl http://127.0.0.1:9808/readyz
curl http://127.0.0.1:9808/v1/status
curl http://127.0.0.1:9808/metrics
```

Global configuration and selectors precede `serve`. The default listen address
is `127.0.0.1` and port is `9808`. `--bind` accepts an IPv4 address and `--port`
accepts 1–65535. Without `--cmd`, the process observes containers only and needs
no state file. All resources are collected by default; repeated `--rsc` options
select a subset, for example `--rsc cpu_percent --rsc mem_usage`.

This initial HTTP interface has no authentication, TLS, UI or remote actions.
Keep it on loopback or a trusted private network. To access it from elsewhere,
place an authenticated TLS reverse proxy in front; setting `--bind 0.0.0.0`
explicitly exposes the endpoint data on every IPv4 interface. Container names,
IDs, statuses and measurements are visible. Configuration, command text and raw
exception messages are not exposed by the API.

## Optional remediation

```sh
monit-docker --name 'web*' serve --interval 30 \
  --state-file /var/lib/monit-docker/web.json --cooldown 300 \
  --dry-run --cmd-if 'mem_percent > 90 ? restart'
```

Rules require an explicit `--state-file`, including in dry run. Review behavior,
then remove `--dry-run` to execute actions. Each cycle acquires the same state
lock and reserves the same persistent cooldowns as [cron](cron.md). The lock is
released between cycles. Using the same file lets cron and serve coordinate;
separate files do not coordinate actions. Lock contention (117), state failures
(118), Docker errors or action failures mark that cycle as failed. The process
remains alive and retries on the next cycle. The successful actions of a partial
cycle are not rolled back, and their reservations remain on disk.

Dry-run reservations are simulated within each cycle and discarded afterwards.
Action-decision counters appear in the API and metrics; individual command text
is not served. Exec failures retain the agent error code 116 in this long-running
mode; there is no process-exit propagation option.

## Cache, freshness and lifecycle

The latest successful cycle's snapshots are held in a synchronized memory cache.
They are replaced as a complete set; requests never see half of a cycle. The
cache holds no history and is empty after a restart. Only cooldown reservations
are persisted; Prometheus can store time-series history externally.

`--stale-after` defaults to `max(90, 3 * interval)` seconds and must be at least
the interval. Freshness uses a monotonic clock, so system clock corrections do
not make old measurements fresh. Timestamps in the API use Unix wall-clock
seconds. Cache age is measured from successful cycle completion, not separately
for each container's sampling time.

Before the first success, after a failed cycle, or when data expires,
`ready` is false and container measurements are omitted from both status and
metrics. Process counters and the last-success timestamp remain available.
During a cycle, the previous result remains available while it is still fresh.
The HTTP listener remains responsive during a slow or blocked Docker call.

SIGINT and SIGTERM request shutdown: finish the current cycle, stop scheduling,
close the HTTP socket and exit successfully. Existing Docker client timeouts
still apply; there is no cycle-wide deadline or forced cancellation. A hung
Docker call can therefore delay shutdown. The HTTP endpoint has eight request
workers, five-second socket timeouts and closes surplus connections. Use a
reverse proxy for public-facing HTTP limits and supervision for process recovery.
Configuration is loaded at startup; restart the process after changing it.

## Read-only HTTP contract

| Endpoint | Success / failure | Purpose |
| --- | --- | --- |
| `/healthz` | 200 while HTTP is responsive | `{"alive": true}`; does not claim Docker is healthy |
| `/readyz` | 200 ready, otherwise 503 | `{"ready": true}` or `{"ready": false}` |
| `/v1/status` | 200, including degraded states | Cached status described below |
| `/metrics` | 200, including degraded states | [Available Prometheus metrics](metrics.md) |

GET and HEAD are supported. POST, PUT, PATCH, DELETE and OPTIONS return 405;
unknown routes return 404. Responses disable caching. There is no HTTP action,
configuration-update or trigger-cycle endpoint.

The `/v1/status` object has `api_version: 1` and these fields. Clients should
ignore additional fields, so later additions need not change the URL version.

| Field | Meaning |
| --- | --- |
| `running` | A collection/remediation cycle is in progress |
| `ready` | Latest completed cycle succeeded and is fresh |
| `last_cycle_success` | Outcome of the last completed cycle; false at startup |
| `last_cycle_finished_at` | Unix seconds of last completion, or null |
| `last_success_at` | Unix seconds of last successful completion, or null |
| `age_seconds` | Monotonic age of the last success, or null |
| `last_error_code` | Agent error code for the last failed cycle, otherwise null |
| `cycles_total` / `errors_total` | Completed / failed cycles since process start |
| `actions` | Counters for `executed`, `cooldown` and `dry-run` decisions |
| `containers` | Fresh complete snapshot list; empty when not ready |

Each container has `id`, `name`, `status`, `pid`, `mem_usage`, `mem_limit`,
`mem_percent`, `cpu_percent`, `io_read`, `io_write`, `net_tx`, and `net_rx`.
Byte fields and percentages are raw numbers, not formatted strings. Unknown or
unrequested fields are null. A successful metadata-only cycle can be ready even
if no numerical metrics were requested. No matching container produces the
existing error 114 and makes the monitor unready.
