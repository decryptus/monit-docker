# OOM, repeated starts and PID checks

These optional checks are available since 0.0.68. They use
Docker metadata and statistics, without running commands inside containers.
Existing default checks make no additional event-history request.

| Resource | Meaning |
| --- | --- |
| `oom_events` | Docker OOM events retained in the recent window, including after a container restart. |
| `starts_recent` | Container starts in that window: initial start, explicit start/restart and Docker restart-policy starts. |
| `pids_current` | Current cgroup process/thread count. This is not the host PID of the container's main process (`pid`). |
| `pids_limit` | Configured finite Docker PID limit; `null` when unset or unlimited. |
| `pids_percent` | `100 * pids_current / pids_limit`; `null` without a finite configured limit. |

## Read checks

```sh
monit-docker --name 'web*' --event-window 300 stats \
  --rsc oom_events --rsc starts_recent \
  --rsc pids_current --rsc pids_limit --rsc pids_percent
```

`--event-window` is a global option before `stats`, `monit`, `cron` or `serve`.
It accepts 1–86400 seconds and defaults to 300. One query reads the Docker event
buffer per cycle for all selected containers, only when an event resource is
requested explicitly or by a rule. PID checks share the existing stats stream.
No extra service, database, event thread or persistent state file is added.

For the UI and Prometheus, explicitly select the desired resources, for example:

```sh
monit-docker --event-window 300 serve \
  --rsc status --rsc health --rsc cpu_percent --rsc mem_usage --rsc mem_limit \
  --rsc mem_percent --rsc oom_events --rsc starts_recent --rsc pids_current
```

Selecting any PID resource also supplies the count, limit and percentage in
the API. The UI displays them when available and shows incomplete event history
explicitly. Regular UI authentication and action controls are unchanged.

## Rules and actions

These numeric resources work with existing YAML condition/command aliases,
`check-config`, `monit`, `cron`, `serve`, dry runs, delays and cooldowns.
For reusable YAML aliases:

```yaml
conditions:
  recent_oom:
    expr: ['oom_events > 0']
  frequent_starts:
    expr: ['starts_recent >= 3']
  pid_pressure:
    expr: ['pids_percent > 90']
```

For example, to preview stopping a service that started three times in five minutes:

```sh
monit-docker --name web --event-window 300 cron \
  --state-file /var/lib/monit-docker/web.json --dry-run \
  --cmd-if 'starts_recent >= 3 ? stop'
```

For PID pressure, a possible condition is `pids_percent > 90`. Configure a finite
Docker `pids_limit` first, or use an absolute `pids_current` threshold instead.
An unavailable value is an error (115) when used in a rule, never a healthy zero.
Event-only rules also work on stopped containers; PID rules require a running
or paused container with supported PID accounting.

**Starts are not the automatic restart budget.** The initial start counts,
and a start/stop sequence counts on its next start. This observes Docker and
operator activity; it does not limit Docker's restart policy. The existing
`--max-restarts` guard applies only to monit-docker's own automatic restarts.
A rule action based on these checks uses the same persistent budget and audit
journal as other automatic actions. An OOM is not automatically a reason to
restart: the underlying memory pressure may need intervention.

## Bounded history and unavailable data

Docker exposes only its last **256 events across the daemon**, including events
unrelated to monitored containers. The collector deliberately reads this buffer
without server-side filtering so unrelated activity cannot hide retention overflow.
It bounds the response to 2 MiB, 256 events and 64 KiB per event, uses a five-second
HTTP idle timeout and checks a five-second read budget between chunks. This is not
a hard whole-cycle deadline. Event attributes and application output are discarded.

The interval is `(event_window_end - event_window_seconds, event_window_end]`.
The cutoff is a whole second in the past, so an event may appear on the next cycle.
Counts use full container IDs: a replacement with the same name has its own history.

If a full buffer begins after the window's lower bound, both event counts are
`null`, and `event_history_complete` is 0. The corresponding Prometheus count
samples are omitted; rules needing those counts fail instead of acting on a
partial count. An empty retained buffer reports zero, not proof of historical health.

The history belongs to the **current Docker daemon**, not a durable event log.
A daemon restart can erase it; `event_history_complete` only reports detected
buffer truncation and cannot certify history across daemon downtime/restarts.
Long agent outages, busy hosts and windows longer than retained history may miss
events. Keep clocks synchronized with a remote Docker host. Transport, malformed
event and size-limit failures report collection error 115.

No event occurrence is inferred from exit code 137 alone. `oom_events` counts
Docker OOM notifications, which do not always mean the container's main process
exited. A healthy restart does not erase a retained OOM within the window.

## Alerts, notifications and journal

The metrics are window **gauges**, not cumulative counters; do not apply `rate()`
to `oom_events` or `starts_recent`. The supplied alert examples detect OOMs,
at least three starts in a 300-second window, high PID usage and truncated history.
They activate only when the corresponding checks are enabled. Existing
Alertmanager/email/Slack/Redis/HTTP notification integrations can deliver them.

Actions and their outcomes are recorded in the existing audit journal. To journal
the alerts themselves, configure the existing private Alertmanager audit receiver
or the audited notification adapters as described in [the audit guide](audit.md).
Reading an event window does not append all its events repeatedly to the journal.

Docker references: [events and retention](https://docs.docker.com/reference/cli/docker/system/events/)
and [PID statistics](https://docs.docker.com/reference/cli/docker/container/stats/).
