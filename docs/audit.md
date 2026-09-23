# Action and notification journal

This feature is available in the source tree after 0.0.64. Build the agent and UI
from this revision to try it; the published 0.0.64 images do not accept these flags.

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
the durable journal is exported administratively, not exposed through a public
HTTP download route.

## Storage and recovery

The agent enables its journal when configured with rules, manual actions or the
notification webhook. Use a persistent mount and an explicit path:

```sh
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  --audit-max-bytes 10485760 --audit-files 5 \
  serve --state-file /var/lib/monit-docker/state.json --cmd 'status == exited ? start'
```

Without `--audit-file` (or `MONIT_DOCKER_AUDIT_FILE`), the file is
`audit/events.jsonl` beside the configured state file, otherwise
`$XDG_STATE_HOME/monit-docker/audit/events.jsonl` (default state directory:
`~/.local/state`). Mount the containing directory, not only the active file.
The existing manual-actions Compose example already persists the agent state
directory. Deleting its volume also deletes its journal.

Defaults retain **five files of at most 10 MiB each**, including the active file:
`events.jsonl`, `.1` through `.4`. Oldest events expire by size, not age. Files use
0600; newly created directories use 0700. Rotation and exports share a local
filesystem lock, and writes are flushed before an operation begins. Use a local
Linux filesystem; distributed/network filesystem guarantees are not assumed.
Changing retention settings does not delete backups outside the new range; remove
obsolete archives administratively after exporting them.

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

For an authenticated UI, rebuild both images from this revision, ensure the
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

Export reads a consistent snapshot of retained files, oldest first. CSV values
that could be interpreted as spreadsheet formulas are escaped. Protect exported
files with restrictive permissions, for example run `umask 077` first.

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
