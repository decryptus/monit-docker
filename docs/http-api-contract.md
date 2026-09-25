# HTTP API and metrics compatibility baseline

This reference describes the **0.0.77 behavior** of the private agent listener.
It is a tested 0.0.x baseline, not an approved 1.0 stability promise. The
[roadmap](roadmap.md) tracks the remaining compatibility decisions. See the
[configuration and CLI baseline](config-cli-contract.md) for startup options.

## Scope and transport

`serve` listens on `127.0.0.1:9808` by default. HTTPdis handles routing and
serialization; Sonicprobe bounds the worker pool. These routes describe the
agent directly. The [optional Nginx UI proxy](ui.md) exposes a smaller set of
routes, adds authentication and may return its own error responses. In
particular, the public demo does not forward `/metrics`, `/healthz`, `/readyz`
or the notification receiver. Use the private agent address in the examples.

Agent JSON responses use `application/json; charset=utf-8`, a trailing newline,
and finite JSON numbers. Normal route responses and adapter errors include:

```text
Cache-Control: no-store
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
```

`HEAD` on read routes has the corresponding status, content type and
representation length, but no body. A separate GET may differ as time, queued
actions or the journal advance. There is no CORS preflight endpoint: `OPTIONS`
returns 405 without `Access-Control-Allow-Origin`. No route changes configuration
or explicitly starts a monitoring cycle. Reads never execute Docker actions.

| Route | Methods | Availability / authentication | Normal response |
| --- | --- | --- | --- |
| `/healthz` | GET, HEAD | Always, no agent credential | 200 `{"alive":true}` |
| `/readyz` | GET, HEAD | Always, no agent credential | 200 `{"ready":true}` or 503 `{"ready":false}` |
| `/v1/status` | GET, HEAD | Always, no agent credential | 200 cached status, including degraded states |
| `/metrics` | GET, HEAD | Always, no agent credential | 200 Prometheus text, including degraded states |
| `/v1/actions` | POST | Manual actions enabled; action token and exact Origin | 202 request record |
| `/v1/audit` | GET, HEAD | Journal reader enabled; audit token and actor | 200 bounded event page |
| `/v1/audit/export` | GET, HEAD | Same journal authentication | 200 JSONL or CSV attachment |
| `/v1/notifications` | POST | Notification audit receiver enabled; separate bearer token | 202 receipt count |

Use the canonical paths above, without trailing slashes. Unknown reads return
404. POST/PUT/PATCH/DELETE/OPTIONS on read-only routes return 405. A GET on the
POST-only action route currently returns 404, not 405. Disabled actions and
notifications return 405; a disabled journal reader returns 404. A 405 response
has an `Allow` header based on the declared route, even if its capability is
disabled. Arbitrary unsupported HTTP verbs are not part of this baseline.
Unexpected handler failures return 500 `{"error":"internal_error"}` rather
than a raw exception message.

## Cached status

Clients must ignore additional JSON fields and inspect capability objects rather
than infer support from the URL version. Existing field names and meanings below
are covered by regression tests; JSON object ordering is not an interface.

| Field | JSON type | Meaning |
| --- | --- | --- |
| `api_version` | integer | Currently 1; independent of package and journal versions |
| `running` | boolean | A monitoring cycle is collecting or applying rules; not a manual-action activity flag |
| `ready` | boolean | Last completed cycle succeeded and its monotonic age is at most `stale_after` |
| `last_cycle_success` | boolean | False at startup; outcome of the last completed cycle |
| `last_cycle_finished_at` | number or null | Last cycle completion, Unix seconds |
| `last_success_at` | number or null | Last successful completion, Unix seconds; retained after later failure/staleness |
| `age_seconds` | number or null | Nonnegative monotonic age of that success; null before first success or after invalidation for manual execution |
| `last_error_code` | integer or null | Agent failure code, not an HTTP status; no raw exception message |
| `cycles_total`, `errors_total` | integers | Completed cycles / failed cycles since process startup |
| `actions` | object | Automatic decision counters, described below |
| `containers` | array | Complete fresh snapshot, or `[]` when not ready |
| `manual_actions` | object | Always present in this baseline; `{"enabled":false}` when disabled |
| `audit_enabled` | boolean | Whether the authenticated journal reader is available, not merely whether audit writing is configured |

`actions` always has `executed`, `cooldown`, `pending`, `restart-limit`,
`maintenance` and `dry-run`, initially zero. These count automatic rule decisions,
not manual requests. One rule can produce multiple decisions. Successful actions
before a later failure remain counted; failed actions are not counted as
`executed`. All process counters reset on restart.

Starting a new cycle does not immediately invalidate a still-fresh previous
success: `running:true` and `ready:true` may coexist. At startup, after a failed
cycle or after staleness, status and metrics remain HTTP 200 while readiness is
503 and container measurements are withheld. Staleness alone does not increment
errors or create a failure code. A failed cycle clears the exposed containers;
its prior success timestamp and age can still be present. A manual execution
invalidates freshness before calling Docker, including if the attempt fails.
Successful metadata-only collection can be ready without numerical measurements.

### Container fields

Each snapshot includes the fields below; unavailable or unrequested numerical
fields are `null`, not formatted strings or synthetic zeroes. Container order is
not guaranteed. The collector uses full Docker IDs and names without a leading
slash. A replacement container has a new identity.

| Fields | JSON type / units |
| --- | --- |
| `id`, `name`, `status` | Strings; Docker identity, name and state |
| `pid` | Integer or null; host PID of the container's initial process |
| `health` | `healthy`, `unhealthy`, `starting`, `none` or `unknown` |
| `mem_usage`, `mem_limit`, `io_read`, `io_write`, `net_rx`, `net_tx` | Numbers or null, bytes |
| `mem_percent`, `cpu_percent` | Numbers or null, percentages; CPU may exceed 100 |
| `oom_events`, `starts_recent` | Nonnegative integers or null, counts in the requested window |
| `pids_current`, `pids_limit` | Nonnegative integers or null; process/thread count and finite cgroup limit |
| `pids_percent` | Number or null, percentage of the finite PID limit |
| `event_window_seconds`, `event_window_end` | Nonnegative integers or null; window duration and cutoff in Unix seconds |
| `event_history_complete` | Integer 0 or 1, or null; retained daemon-buffer completeness, not durable history |
| `manual_actions_protected` | Boolean, false by default; protects against manual actions only |
| `restart_attempts`, `restart_limit` | Nonnegative / positive integers or null; automatic restart policy metadata |
| `maintenance_active` | Boolean; whether maintenance was active at collection time |
| `maintenance_until` | Number or null, configured expiry in Unix seconds |
| `filesystems` | Array, empty when none were collected |

Each filesystem object has string `group` and `path`, plus these measurements:

- `disk_usage`, `disk_available`, `disk_total`: bytes or null.
- `disk_percent`: used / (used + available) × 100, or null.
- `inode_usage`, `inode_available`, `inode_total`: inode counts or null.
- `inode_percent`: used / total × 100, or null.
- `fs_mode`: `ro`, `rw` or null.
- `fs_readable`, `fs_writable`, `fs_executable`: integer 0 or 1, or null,
  for the explicitly configured access identity.

See [filesystem semantics](filesystems.md), [ACL-aware access checks](access-checks.md)
and [runtime checks](runtime-checks.md). Maintenance indicators are sampled state,
not a live countdown; repeated GET requests do not update the snapshot.

## Manual actions

Send exactly three string fields: `request_id` (32 lowercase hexadecimal
characters), `container_id` (full 64 lowercase hexadecimal characters) and
`action`. Discover available commands through
`manual_actions.allowed_states`; its values are arrays of acceptable cached
Docker states. Do not send shell commands, aliases or extra arguments.

| Action | Accepted cached states |
| --- | --- |
| `start` | `created`, `exited` |
| `stop` | `running`, `restarting` |
| `restart` | `running` |
| `restart-reset` | `created`, `running`, `paused`, `restarting`, `exited`, `dead`; also requires a nonzero restart budget counter |
| `maintenance-15m`, `maintenance-1h`, `maintenance-off` | Same states as restart-reset; present only when manual maintenance is enabled |

The request needs exactly one `X-Monit-Action-Token` and one `Origin`, matching
the configured values. With trusted proxy attribution enabled, exactly one
nonblank `X-Monit-Actor` is required (at most 128 characters, no ASCII controls
or DEL). Otherwise the actor is `anonymous` and a supplied actor is ignored.
Credentials are not returned in status. A trusted proxy must replace client actor
headers; see [authentication setup](ui.md).

The body limit is **1024 bytes**, inclusive. Actions currently require
`Content-Type: application/json` without parameters, so even
`application/json; charset=utf-8` is rejected. The body must have a single ASCII
decimal `Content-Length` of at most five digits and no `Transfer-Encoding`.
Zero length is rejected with 413. JSON decoding/framing failures return 400;
read timeouts return 408. These are current adapter behaviors, not a general
HTTP parser specification.

### Acceptance, completion and retries

202 returns a record with `request_id`, `container_id`, `action`, `actor`,
`container_name`, `status`, `submitted_at`, `finished_at`, `error` and `error_code`.
Timestamps are Unix-second numbers; `finished_at`, `error` and `error_code` start
as null. States are `queued`, `running`, `succeeded`, `failed`. Poll
`manual_actions.recent` in `/v1/status` for results; there is no GET-by-request-ID
route. Records are in submission order and at most 32 are retained in memory.

One action may be queued or running across the agent. The scheduler executes it
between cycles and refreshes measurements after an execution attempt. Queue
expiry is checked when dequeuing, after more than 60 monotonic seconds; it is not
a completion deadline. Protection, selection, state and cooldown are checked
again before the mutation, so an accepted request may later fail.

A retained ID with the same three fields **and actor** returns its existing record
with HTTP 202, even after completion or if the agent is now unready. It does not
execute again. Different payload/actor with that ID returns 409. This is bounded,
process-local deduplication: eviction or restart removes that guarantee. A timeout
does not establish whether an action executed. Inspect status and the retained
journal before deciding whether to submit another mutation.

| Submission status | `error` reasons |
| --- | --- |
| 400 | `invalid_request`, `invalid_length`, `invalid_json` |
| 403 | `forbidden`, `origin_rejected`, `container_protected` |
| 405 | `method_not_allowed` |
| 408 | `request_timeout` |
| 409 | `request_id_conflict`, `busy`, `not_ready`, `not_selected`, `state_changed`, `no_restart_attempts` |
| 413 | `request_too_large` |
| 415 | `json_required` |
| 503 | `audit_unavailable` |

Transport/authentication failures are not action records. Audit failures can
replace a rejection response with 503. Completed record reasons include
`expired`, `audit_unavailable`, `unsupported_action`, `busy`, `not_selected`,
`container_protected`, `state_changed`, `no_restart_attempts`, `cooldown` and `execution_failed`.
`execution_failed` can carry an agent `error_code`; raw exception text is not
exposed. Reasons and state names remain English machine-readable values.

## Journal pages and exports

Both routes require exactly one `X-Monit-Audit-Token` and one valid
`X-Monit-Actor`, with the same actor length/control restrictions as above.
Authentication applies even to an empty journal. HTTP 200 returns:

| Field | Type / meaning |
| --- | --- |
| `records` | Array of retained events, newest file records first |
| `next_cursor` | Opaque string for the next older page, or null when exhausted |
| `page_cursor` | Opaque string replaying this page's snapshot, for export |
| `scanned_bytes` | Nonnegative integer, journal bytes read including identity checks |
| `limit` | Integer, currently 100 records per page |

Accept an empty `records` array with a non-null `next_cursor`: a bounded scan may
find no matches yet. Reads scan at most 1 MiB and collect at most 512 KiB of encoded
event data per page, plus response overhead. No client-selectable page size exists.
The [audit guide](audit.md) defines event fields, escaping and retention. Journal
schema version 2 is independent of HTTP `api_version`; its longer-term evolution
policy remains a separate roadmap item.

| Query parameter | Meaning |
| --- | --- |
| `since`, `until` | Inclusive ISO timestamps with a timezone; since must not exceed until |
| `container` | Case-insensitive substring of container name or ID, not a glob or regular expression |
| `source` | `manual` or `automatic` |
| `category` | `action` or `notification` |
| `result` | `pending`, `succeeded`, `failed`, `rejected`, `skipped`, `simulated`, `accepted`, `received` |
| `correlation_id` | Exact, case-sensitive ID |
| `cursor` | Opaque signed continuation or page token |
| `format` | `jsonl` (default) or `csv`; affects the export route, not the JSON page envelope |

Filters combine with AND. Unknown or repeated query keys are rejected; empty
values are ignored. Each nonempty filter is at most 256 printable characters.
The encoded query is limited to 32768 characters and a cursor to 24000.
Cursors are bound to the authenticated actor and exact filter values, exclude
later appends, and expire after 15 minutes or loss of unread snapshot data.
Changing only export format is permitted. An agent restart changes the signing
key: old cursors become invalid, with 400 rather than 410.

Errors are `{"error":"REASON"}`: 400 `invalid_filters` or `invalid_cursor`,
403 `forbidden`, 404 `not_found` when disabled, 410 `cursor_expired`, or 503
`audit_busy` / `audit_unavailable`. A 503 is an explicit read failure, never an
empty successful history. Cursor and storage errors do not reveal file paths.

`/v1/audit/export` returns one bounded page, not the whole journal. Pass the
displayed `page_cursor` and the same filters. Its media type is
`application/x-ndjson; charset=utf-8` or `text/csv; charset=utf-8`, with
`Content-Disposition: attachment; filename="monit-docker-events.jsonl"` (or
`.csv`). JSONL has one event per line; CSV has the documented event-column header.
An empty JSONL export is empty; an empty CSV export still has its header.
Use the administrative `audit-export` command for all retained events.

## Notification receipts

`POST /v1/notifications` accepts the Alertmanager version-4 webhook shape, with
`Authorization: Bearer TOKEN` using the separately configured audit token. No
Origin or actor header is required. It accepts JSON content-type parameters and
has an inclusive **65536-byte** limit; length/framing rules otherwise match
actions. Manual actions do not need to be enabled for this route.

Required top-level fields are `version: "4"`, `status: "firing"` or `"resolved"`,
string `receiver` (at most 256 characters), string `groupKey` (at most 2048), and
`alerts` (1..100 objects). `truncatedAlerts` must be zero if supplied. Each alert
requires a firing/resolved `status` and a nonempty string `labels.alertname` of at
most 256 characters. Optional `fingerprint`, `startsAt`, `endsAt` are strings of
at most 256 characters; timestamps are not parsed here. Extra webhook fields are
accepted but arbitrary labels/annotations are not copied into the journal.

The whole batch is validated before writes. HTTP 202 returns
`{"recorded":N,"delivery_status":"not_reported"}` after receipt recording; it
does not prove email/Slack delivery. Invalid payloads return 400
`invalid_notification`; storage failure returns 503 `audit_unavailable`.
A write failure partway through a valid batch may leave earlier receipts stored.
Retries can repeat receipts; `notification_id` provides correlation, not durable
deduplication. See [notification audit semantics](audit.md).

## Prometheus contract

The [metrics reference](metrics.md) is the name/type/unit/label catalogue for this
baseline. `/metrics` uses `text/plain; version=0.0.4; charset=utf-8` with a final
newline. Required names, declared types, label keys and representative values
are covered by `tests/test_api_contract.py` alongside existing collector tests.

- Agent counters reset on process restart; Docker I/O/network counters have their
  own reset lifecycle. Percentages use 0..100 units, not 0..1 ratios; CPU can exceed 100.
- Container samples require ready, fresh cached data. Unknown or unrequested
  measurements are omitted; a known zero still emits a sample. Empty metric
  families can retain HELP/TYPE lines. Last-success metadata is absent until the
  first success and remains present after subsequent degradation.
- Health emits the current state with value 1, not five one-hot zero/one series.
  Boolean metrics use numeric 0/1. Event-window counts are gauges, not counters.
- Container labels are `id`, `name`, plus only the specific keys in the catalogue.
  Manual protection, actor, reason and arbitrary Docker labels are not metrics.
- Label backslashes, newlines and double quotes are escaped. Sample order, label
  order and HELP prose are not compatibility promises. There are no explicit
  sample timestamps. New metric families or JSON fields may be added.

## curl examples

Read the private agent (these four calls never execute a monitoring cycle):

```sh
AGENT_URL='http://127.0.0.1:9808'
curl --fail-with-body --silent --show-error "$AGENT_URL/healthz"
curl --fail-with-body --silent --show-error "$AGENT_URL/readyz"
curl --fail-with-body --silent --show-error "$AGENT_URL/v1/status"
curl --fail-with-body --silent --show-error "$AGENT_URL/metrics"
```

For a direct, authorized mutation, configure actions first and supply your actual
token, allowed origin and selected full container ID. Keep the generated request
ID to correlate status and journal records. This example **restarts a container**:

```sh
ACTION_TOKEN=$(cat /path/to/private/action-token)
ACTION_ORIGIN='https://monitor.example'
CONTAINER_ID='replace-with-a-selected-64-character-container-id'
REQUEST_ID=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
curl --fail-with-body --silent --show-error "$AGENT_URL/v1/actions" \
  -H 'Content-Type: application/json' \
  -H "X-Monit-Action-Token: $ACTION_TOKEN" \
  -H "Origin: $ACTION_ORIGIN" \
  -H 'X-Monit-Actor: operator' \
  --data "{\"request_id\":\"$REQUEST_ID\",\"container_id\":\"$CONTAINER_ID\",\"action\":\"restart\"}"
curl --fail-with-body --silent --show-error "$AGENT_URL/v1/status"
```

Browse the journal using its separate token, then export exactly the same page
(Python reads the saved JSON; no extra JSON command-line utility is needed):

```sh
AUDIT_TOKEN=$(cat /path/to/private/audit-read-token)
curl --fail-with-body --silent --show-error --get "$AGENT_URL/v1/audit" \
  -H "X-Monit-Audit-Token: $AUDIT_TOKEN" -H 'X-Monit-Actor: operator' \
  --data-urlencode 'category=action' --data-urlencode "correlation_id=$REQUEST_ID" \
  --output page.json
PAGE_CURSOR=$(python3 -c 'import json; print(json.load(open("page.json"))["page_cursor"])')
curl --fail-with-body --silent --show-error --get "$AGENT_URL/v1/audit/export" \
  -H "X-Monit-Audit-Token: $AUDIT_TOKEN" -H 'X-Monit-Actor: operator' \
  --data-urlencode 'category=action' --data-urlencode "correlation_id=$REQUEST_ID" \
  --data-urlencode "cursor=$PAGE_CURSOR" --data-urlencode 'format=csv' \
  --output action-events.csv
```

These are direct private-agent examples, not commands for the public demo or a
proxy using browser/session authentication. See [UI setup](ui.md) for proxy rules.

## Decisions still open before 1.0

- Unify content-type parameter handling between actions and notifications.
- Decide whether wrong methods on known routes should consistently return 405.
- Define support for any additional HTTP verbs, parser edge cases or path aliases;
  do not rely on accidental acceptance as a stable interface.
- Approve which field/value changes require an API version change, and define
  deprecation/migration rules before removing or changing existing semantics.
- Define journal schema evolution separately from the page envelope and the
  package version; bounded cursors and in-memory request IDs are not durable APIs.

This documentation deliberately records current differences instead of changing
behavior while inventorying the interfaces.
