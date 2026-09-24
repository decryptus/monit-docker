# Action and notification journal

Available since **0.0.65** in the Python package and versioned Docker images.
Use matching agent and UI versions; 0.0.64 does not accept the audit options.

Actions and configured notification adapters write versioned JSONL events to a
local journal, independently of diagnostic log verbosity. Every event includes a
unique ID, UTC timestamp, host, category, origin, actor and result. Lifecycle
records share a correlation ID. The journal is also emitted as JSON lines on
stderr for collection by Docker logging drivers or an external log collector.

## What is recorded

| Operation | Events and results | Actor |
| --- | --- | --- |
| CLI `monit` invocation | Started; succeeded or failed | Local operating-system username |
| Automatic rule action | Started; succeeded or failed, duration and error/exec exit code when known | `rule-engine` (`cron` for the cron subcommand) |
| Matched rule delayed, in cooldown or dry-run | Skipped or simulated, with reason | `rule-engine` |
| Authenticated manual request | Queued, started, completed or rejected, including protection/cooldown/expiry | Trusted proxy username, otherwise `anonymous` |
| Alertmanager audit webhook | Each firing/resolved alert received; delivery `not_reported` | `alertmanager` |
| Redis notification bridge | Send started; Redis accepted or send failed | `alertmanager-redis-bridge` |
| DWho HTTP sender | Send started; remote API accepted or send failed | Local operating-system username |

A successful Docker call does not assert application health. A timeout may mean
an operation reached Docker or the destination but its acknowledgement was lost.
Do not blindly replay an action from a failed or incomplete journal entry.
Commands, command output, raw rule expressions, tokens, request bodies, alert
annotations and destination URLs are excluded from this journal. Rule and command
identifiers are SHA-256 fingerprints. Names of actors, containers and alerts are
included; choose names that do not contain secrets. Existing diagnostic logs are
separate and can still contain command details.

The one-shot `monit` subcommand is attributed as manual, and `serve`/`cron` rule
execution as automatic. If an external scheduler invokes `monit`, use the `cron`
subcommand for accurate automatic attribution.

Only operations passing through these components are recorded. Docker commands
run elsewhere, reverse-proxy authentication failures and the demo host's recovery
timer are recorded by their own services. Metrics polling and unmatched rules do
not produce audit events. The public UI retains its bounded recent-action view;
the durable journal stays private. An optional authenticated journal page is
available since 0.0.66, as described below.

## Text encoding shared by all outputs

Schema version **2** prepares every textual value when an event is created,
before storage, stderr collection, export or HTTPS forwarding. This includes
usernames, container/alert names and future textual fields. Non-printable Unicode
characters (including newlines, terminal controls and bidirectional formatting)
become visible `\uXXXX` or `\UXXXXXXXX` escapes. Spreadsheet formula prefixes
`=`, `+`, `-`, `@` and their fullwidth equivalents are escaped at the beginning of
a value, including after ASCII spaces. Other leading whitespace is itself escaped.
Printable names, accents and emoji remain unchanged.

Literal backslashes are doubled: an actual newline becomes `\u000a`, whereas the
literal text `\u000a` becomes `\\u000a`. This reversible convention preserves
distinct identities without Unicode normalization or stripping characters. JSON
adds its own escaping around these values; a JSON parser returns the same display
text as a CSV reader. Consumers should display that text without decoding the
visible escapes back into controls or formulas.

Schema 1 journals remain readable: values are converted to schema 2 in memory
without rewriting archives. Schema 2 records are validated, not escaped again.
New exporters must consume `AuditJournal.read()` / `prepare_record()` output and
preserve these values. Continue using the target format's serializer (for example,
`csv.DictWriter`); this common policy does not replace HTML or other contextual
escaping required by a destination.

## Storage and recovery

The agent enables its journal when configured with rules, manual actions or the
notification webhook. Use a persistent mount and an explicit path:

```sh
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  --audit-max-bytes 5242880 --audit-files 5 \
  serve --state-file /var/lib/monit-docker/state.json --cmd 'status == exited ? start'
```

Without `--audit-file` (or `MONIT_DOCKER_AUDIT_FILE`), the file is
`audit/events.jsonl` beside the configured state file, otherwise
`$XDG_STATE_HOME/monit-docker/audit/events.jsonl` (default state directory:
`~/.local/state`). Mount the containing directory, not only the active file.
The existing manual-actions Compose example already persists the agent state
directory. Deleting its volume also deletes its journal.

From 0.0.66, the default retains **five files of at most 5 MiB each** (25 MiB total),
including the active file:
`events.jsonl`, `.1` through `.4`. Oldest events expire by size, not age. Files use
0600; newly created directories use 0700. Rotation and exports share a local
filesystem lock, and writes are flushed before an operation begins. Use a local
Linux filesystem; distributed/network filesystem guarantees are not assumed.
Published 0.0.65 defaults to 10 MiB per file; pass `--audit-max-bytes 5242880`
explicitly to use the smaller limit on that release. A lower limit applies to
subsequent writes and rotations; it does not truncate existing larger archives.
Export larger archives with their original `--audit-max-bytes` setting before
archiving them separately or letting retention replace them. Changing the file
count does not delete backups outside the new range; remove obsolete archives
administratively after exporting them.

If the initial write fails, the action or notification send does not begin. If
the completion write fails after an operation, its real outcome is emitted to
stderr with a critical diagnostic; the operation is not repeated to repair the
journal. A crash can leave an unmatched `started` event: its outcome is unknown.
A truncated final line blocks further appends. Stop writers, preserve/export the
file for investigation, and repair or archive it before restarting. This is a
bounded operational journal, not a tamper-proof compliance archive.

## Who performed a manual action?

Default actor: `anonymous`. An action token authenticates the proxy connection;
it does not identify a person. This is also the honest identity on the public demo.

For an authenticated UI, use agent and UI images 0.0.65 or later, ensure the
agent can only be reached by the trusted proxy, and add `--trust-proxy-user` to
`serve --allow-actions ...`. The shipped Nginx proxy overwrites `X-Monit-Actor`
with its authenticated `$remote_user`. Missing/invalid identity is rejected when
this option is enabled. Without the option the header is ignored. Never enable
it behind a proxy that passes a client-supplied actor header through unchanged.
A shared account identifies that account, not an individual.

## Export or forward retained events

Global audit options go **before** the subcommand. These commands do not connect
to Docker. Supply the same retention settings if changed from the defaults.

```sh
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  audit-export > events.jsonl
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  audit-export --format csv --category action --since 2026-09-01T00:00:00Z > actions.csv
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  audit-send --url https://logs.example.org/events --token-file /run/secrets/logs-token
```

Export reads a consistent snapshot of retained files, oldest first. All formats
use the shared text encoding described above. Protect exported files with
restrictive permissions, for example run `umask 077` first.

`audit-send` POSTs one JSON event per request, verifies HTTPS certificates, refuses
redirects and supplies `Idempotency-Key: <event_id>`. The optional token file holds
a Bearer credential. The receiver must implement this JSON contract; this is not
a native Loki, Elasticsearch or syslog API client. Each 2xx response acknowledges
acceptance, not downstream processing. Failure returns nonzero and preserves the
local journal. Re-running can resend acknowledged events: deduplicate by event ID
at the receiver. There is no background retry queue or durable sending cursor.
For continuous forwarding, collect the structured stderr events with your
existing logging service; configure that collector's buffering and retention.
Forwarding does not journal its own exports recursively.

## Include Alertmanager notifications

An additional webhook on **every receiver you want to journal** mirrors its
notifications into the agent. It does not report whether an email or Slack message
was delivered. Those outcomes remain in Alertmanager's delivery logs/metrics;
collect them alongside this journal when channel-level delivery results are needed.
Silenced/inhibited alerts that Alertmanager never notifies are not mirrored.

In `examples/monitoring`, prepare the normal notifications stack as described in
[notifications](notifications.md), then create a separate token:

```sh
mkdir -p notifications.local
chmod 700 notifications.local
python3 -c 'import secrets; print(secrets.token_hex(32))' > notifications.local/audit_token
chmod 644 notifications.local/audit_token
```

The private directory prevents host traversal; the file must be readable by the
non-root Alertmanager process when mounted as a Compose secret. Do not reuse the
manual-action token. Add this entry to each receiver's `webhook_configs` in
`notifications.local/alertmanager.yml`, preserving existing entries/channels:

```yaml
webhook_configs:
  - url: http://monit-docker:9808/v1/notifications
    send_resolved: true
    max_alerts: 0
    http_config:
      authorization:
        type: Bearer
        credentials_file: /run/secrets/audit_token
```

Build and start with the optional source overlay:

```sh
docker compose -f compose.yaml -f compose.notifications.yaml -f compose.audit.yaml up -d --build
```

The overlay persists `agent-audit-data`. The private endpoint accepts authenticated
Alertmanager version 4 JSON (up to 64 KiB and 100 alerts per request), returning 202
after persistence, 400 for invalid payloads or 503 for a storage failure. HTTPdis
0.6.28 enforces the registered route limits before reading the body: 64 KiB for
notifications and 1 KiB for manual actions (HTTP 413 above the limit). Large or
truncated groups are rejected; configure suitably narrow Alertmanager grouping.
Retries can repeat receipts with the same notification ID. Keep this endpoint on
a trusted private network or behind HTTPS; the public UI proxy does not expose it.

The Redis bridge writes the same schema to its own `redis-audit-data` volume at
`/var/lib/monit-docker/redis-events.jsonl`, with JSON also on stderr. This records
Redis acknowledgement, not a consumer processing the stream. The DWho HTTP sender
supports `--audit-file` (default `~/.local/state/monit-docker/http-events.jsonl`) and
`--source automatic` for scheduled invocation; otherwise its source is `manual`.
Its accepted result describes the destination's response, not end-user delivery.
Export adapter files using the agent CLI on the host or a container mounting the
same volume. A central collector can combine these separate journals by event ID.


## Private journal page (since 0.0.66)

The optional **Journal** view at `/logs` displays durable events with date,
container, source, actor, lifecycle phase and result. Filters cover local date
bounds, container name/ID, manual/automatic source, action/notification category
and result. Event details include correlation IDs, safe failure reasons and
notification delivery status. It refreshes only when requested; there is no
polling, external JavaScript, database or additional service.

Since 0.0.74, click a container, Manual action / Automatic action, or result label
to filter the journal. Filters combine, restart at the newest page, and appear
above the events as removable buttons. Clear removes all filters. Container
labels use the full container ID when available, otherwise the name; the API
retains its existing case-insensitive substring matching. Queued and In progress
both select Pending. Exports follow the applied filters and current page.
Quick filters use the applied view and replace unsubmitted form edits.

The API is disabled by default. Enable `serve --audit-read-token-file FILE` and
supply an explicit global `--audit-file PATH`. Use a separate 64-character hex
secret, never the action or notification secret. An authenticated proxy must
**overwrite** `X-Monit-Audit-Token` with that secret and `X-Monit-Actor` with the
signed-in username. Never expose the agent port directly. All authenticated users
of this proxy can read the configured journal; this is not per-container RBAC.
The shipped Nginx proxy uses its existing TLS and Basic authentication. The token
stays on the server and is never sent to browser JavaScript. The public demo does
not enable these routes or expose its journal.

For a **fresh read-only setup**, from `examples/ui`:

```sh
python3 prepare.py --journal --self-signed
docker compose -f compose.yaml -f compose.journal.yaml up -d
```

This uses the matching versioned agent and UI images (0.0.66 or later). Replace the development certificate
with a trusted certificate for remote use. The `state` volume retains the journal
at `/var/lib/monit-docker/audit/events.jsonl`; a fresh read-only agent has no events
until actions/rules or notification receivers are configured. Reading alone does
not execute actions or enable notification delivery.

For an existing actions deployment, preserve the existing secrets and command.
Generate a distinct audit read secret and proxy include in the private secrets
directory (the preparation script refuses to overwrite an existing directory).
Add global `--audit-file /var/lib/monit-docker/audit/events.jsonl` **before**
`serve`, then append `--audit-read-token-file /run/secrets/audit-token` to the
serve options. Reuse the token and proxy-include mounts from
`compose.journal.yaml`. Add `--trust-proxy-user` if manual action attribution
should use the authenticated username. Compose replaces `command` lists: do not
stack the journal and actions overlays without combining their command options.

### Bounded reads and downloads

`GET /v1/audit` returns up to 100 events, newest first, with a signed continuation
cursor. Each request reads at most 1 MiB of journal bytes and retains at most
512 KiB of serialized event data, plus bounded parsing/response overhead. At most
two journal reads run concurrently; other readers receive 503 and may retry.
Opening files briefly takes a nonblocking shared journal lock. File content reads
and serialization run after releasing it, outside the action/monitoring locks.
If a writer holds the journal lock, browsing returns 503 rather than waiting.

A sparse filter can produce an empty page with an **Older events** continuation.
No automatic loop scans the remaining files. Cursors are bound to the filters and
authenticated user, exclude subsequent appends and expire after 15 minutes,
agent restart, or loss of unread files through rotation. Refresh to start a new
snapshot. Externally rewriting/truncating journal files is unsupported.

**Export page · JSONL/CSV** downloads the displayed snapshot only, using
`GET /v1/audit/export` with its page cursor and filters. It preserves the common
escaped text representation. This bounds browser memory and server response size;
use administrative `audit-export` for a complete retained-history export. The
existing CLI full export still takes an in-memory snapshot; these web limits do
not change that behavior. An expired snapshot must be refreshed before export.

The regression suite checks rotation, malformed files, filter/cursor tampering,
proxy identity replacement, writer progress during reads and browser error states.
Run `.github/scripts/benchmark-audit.py` with the project environment to measure a
50 MiB disposable journal, first-page memory and latency, full sparse-search cost
and a concurrent write. Measurements depend on the host and are not latency SLAs.
