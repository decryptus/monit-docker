# Continuous monitoring and HTTP endpoints

Choose **serve mode** when you want monitoring to keep running and expose HTTP
status or Prometheus metrics. For a command that runs once and exits, including
scheduled jobs, use [simple mode](simple.md).

Install version 0.0.56 or newer using the
[installation instructions](https://github.com/decryptus/monit-docker#installation). The process needs Docker
access. Prometheus and Grafana are optional: you can use the HTTP endpoints alone.

For all three services with the dashboard already loaded, use the
[Docker Compose quickstart](compose.md).

`serve` uses the same monitoring engine as `monit` and `cron`. It runs an
immediate cycle, then waits 30 seconds after each completed cycle by default.
Cycles are sequential, including when collection takes longer than the interval.
It adds no runtime dependencies. Read endpoints use an in-memory cache; they never collect Docker statistics or
execute an action. The optional manual-action endpoint queues work for the
monitoring scheduler.

## Start a local monitor

In a terminal, start monitoring all containers and leave the command running:

```sh
monit-docker serve --interval 30
```

In a **second terminal**, check the endpoints:

```sh
curl http://127.0.0.1:9808/healthz
curl -i http://127.0.0.1:9808/readyz
curl http://127.0.0.1:9808/v1/status
curl http://127.0.0.1:9808/metrics
```

Wait for `/readyz` to return HTTP 200 and `{"ready": true}`. A 503 response
means that no successful fresh result is available yet. At least one container
must match. If it stays unready, inspect `/v1/status` and the logs, then see
[troubleshooting](troubleshooting.md). Stop the monitor with Ctrl+C.

To select particular containers, use for example
`monit-docker --name 'web*' serve --interval 30`; replace the pattern with names
that exist on your Docker daemon.

Global configuration and selectors precede `serve`. The default listen address
is `127.0.0.1` and port is `9808`. `--bind` accepts an IPv4 address and `--port`
accepts 1–65535. Without `--cmd`, the process observes containers only and needs
no state file. All resources are collected by default; repeated `--rsc` options
select a subset, for example `--rsc cpu_percent --rsc mem_usage`.

The agent has no read authentication, TLS or embedded UI. The optional
[separate UI component](ui.md) supplies an authenticated Nginx frontend. Manual
start/stop/restart is disabled by default; enabling its API requires explicit
configuration, a proxy secret, an allowed HTTPS origin and persistent state.
Keep it on loopback or a trusted private network. To access it from elsewhere,
place an authenticated TLS reverse proxy in front; setting `--bind 0.0.0.0`
explicitly exposes the endpoint data on every IPv4 interface. Container names,
IDs, statuses and measurements are visible. Configuration, command text and raw
exception messages are not exposed by the API.

## Run serve in Docker

For a local Docker Engine using `/var/run/docker.sock`:

```sh
docker run --rm --name monit-docker-serve \
  -p 127.0.0.1:9808:9808 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  decryptus/monit-docker:0.0.57 monit-docker serve --bind 0.0.0.0
```

Leave this running and use the same `curl` checks from a second terminal on the
host. The listener binds inside the container, while the published host port is
restricted to loopback. Docker socket access allows control of that daemon.
This example observes only; if you add rules, also mount a persistent state
directory and set `--state-file` inside it.

## Add history and charts

1. Configure [Prometheus scraping](metrics.md#prometheus-configuration) and check
   that `up{job="monit-docker"}` and `monit_docker_ready{job="monit-docker"}` are 1.
   The supplied localhost target works when Prometheus runs on the host alongside
   the native process or the Docker example's published port.
2. Add Prometheus as a Grafana data source and [import the example dashboard](grafana.md).
3. Select the data source and filters. Wait for multiple scrapes before expecting
   network/I/O rates or action rates to appear.

If Prometheus runs in a separate container, its loopback is different. Use a
reachable private address or shared container network and adjust the scrape
target accordingly. Prometheus keeps the history; `serve` keeps only its latest
result in memory.

## Optional remediation

```sh
monit-docker --name 'web*' serve --interval 30 \
  --state-file /var/lib/monit-docker/web.json --cooldown 300 \
  --dry-run --cmd-if 'mem_percent > 90 ? restart'
```

For sustained conditions, add [`--trigger-after` and `--max-gap`](trigger-delay.md);
they use the same persistent tracking as cron.

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
cache holds no history and is empty after a restart. Cooldown reservations and optional [trigger observations](trigger-delay.md)
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
Docker call can therefore delay shutdown. HTTPdis dispatches the API routes;
Sonicprobe provides eight request workers and a pending queue of eight requests.
Active requests have five-second socket timeouts. A full queue applies backpressure
to the accept loop; shutdown discards queued connections. Use a
reverse proxy for public-facing HTTP limits and supervision for process recovery.
Configuration is loaded at startup; restart the process after changing it.

Route declarations and JSON/Prometheus serialization live in the HTTP adapter;
the monitoring core does not depend on HTTPdis. HTTPdis is loaded only in `serve`
mode, and Nginx serves the optional UI separately. HTTPdis has a process-global
route registry: run this agent in its own process, rather than embedding it in
another HTTPdis/DWho application. The adapter requires HTTPdis 0.6.28 or later
and Sonicprobe 0.3.53 or later; the Docker image includes their libmagic runtime.

## HTTP contract

| Endpoint | Success / failure | Purpose |
| --- | --- | --- |
| `/healthz` | 200 while HTTP is responsive | `{"alive": true}`; does not claim Docker is healthy |
| `/readyz` | 200 ready, otherwise 503 | `{"ready": true}` or `{"ready": false}` |
| `/v1/status` | 200, including degraded states | Cached status described below |
| `/metrics` | 200, including degraded states | [Available Prometheus metrics](metrics.md) |

GET and HEAD are supported on these read endpoints. POST, PUT, PATCH, DELETE
and OPTIONS return 405 there; unknown read routes return 404. Responses disable
caching. There is no configuration-update or trigger-cycle endpoint. The optional
manual action endpoint is described below.

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
| `actions` | Counters for `executed`, `cooldown`, `pending` and `dry-run` decisions |
| `containers` | Fresh complete snapshot list; empty when not ready |
| `manual_actions` | Optional capability object: `enabled`; when enabled, `allowed_states` and up to 32 `recent` request results |

Each container has `id`, `name`, `status`, `pid`, `mem_usage`, `mem_limit`,
`mem_percent`, `cpu_percent`, `io_read`, `io_write`, `net_tx`, and `net_rx`.
Newer agents also include `manual_actions_protected`, a boolean derived from the
`monit-docker.protected` Docker label. It restricts only manual actions; it is not
a metric or a rule condition. Older agents omit this field. See
[container protection](ui.md#protect-a-container-from-manual-actions).
Byte fields and percentages are raw numbers, not formatted strings. Unknown or
unrequested fields are null. A successful metadata-only cycle can be ready even
if no numerical metrics were requested. No matching container produces the
existing error 114 and makes the monitor unready.


## Optional manual action API

A protected container stays in `GET /v1/status`, but a new manual request for it
returns HTTP **403** with `{"error": "container_protected"}`. The engine also
rechecks protection from fresh metadata before execution: an accepted request
can finish as `failed` with that same reason. Automatic rules are unaffected.

See [UI and authentication setup](ui.md) before enabling writes.
`POST /v1/actions` accepts only an `application/json` body up to 1024 bytes:

```json
{
  "request_id": "e793021f188b443b880a791be38e277d6e",
  "container_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "action": "restart"
}
```

Use a fresh cryptographically random 32-character lowercase hexadecimal request
ID and an exact full 64-character container ID. Extra fields, aliases and command
arguments are rejected. This endpoint requires `X-Monit-Action-Token` supplied by
the trusted proxy and an exact configured `Origin`. The token never appears in
status or frontend files. Missing/invalid credentials or origin return 403;
disabled writes return 405, unsupported content type 415, oversized body 413,
invalid input 400 and rejected preconditions 409.

HTTP 202 returns the request record, including for a retained duplicate ID with
the same payload. Reusing a retained ID for another payload returns 409. Records
contain `request_id`, `container_id`, `action`, `status`, `submitted_at`,
`finished_at`, `error` and `error_code`; timestamps are Unix seconds. Status is
`queued`, `running`, `succeeded` or `failed`. Poll `/v1/status` for the result.
Errors expose a short reason and optional numeric agent code, never raw exception
text. Successful submission is not successful execution. A proxy timeout is an
ambiguous result; clients must not blindly repeat a mutation.

Manual requests execute between monitoring cycles and cause a fresh collection
after an attempt. Measurements are invalidated while a manual operation runs.
Read endpoints remain responsive; they never execute queued work. Existing
`actions` counters and their Prometheus metrics describe autonomous rule
decisions only, not manual requests. The manual queue/results are in memory;
only their per-container cooldown reservations are persisted. The UI guide
describes expiry, restart behavior, selection and autonomous-rule interactions.
