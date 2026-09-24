# Bounded automatic restarts

`cron` and `serve` allow **three automatic Docker `restart` attempts per container
ID by default**, shared across all rules and resolved command aliases in the same
state file. Change the positive integer limit with `--max-restarts`:

```sh
monit-docker --name 'web-*' cron \
  --state-file /var/lib/monit-docker/health.json \
  --cooldown 300 --max-restarts 3 \
  --cmd-if 'health == unhealthy ? restart'
```

The cooldown still spaces attempts of the same rule; it does not limit their
total number. Trigger delay still requires a sustained observed condition.
Pending, unmatched and cooldown-skipped actions consume no restart attempts.
Different rules share the restart budget but retain their individual cooldowns.

Each attempt is saved **before** calling Docker. Failed calls, timeouts and a
crash after reservation consume the attempt: the outcome may be ambiguous, so
retrying without counting it could recreate an infinite loop. A write failure
prevents execution. When a sequence needs more restarts than remain, the entire
rule is skipped before any of its commands execute. Other rules and monitoring
continue. The CLI decision and audit reason are `restart-limit`; this skip is
not a collection failure and `cron` still exits successfully.

## Rearm after intervention

The counter stays latched until explicitly reset. It does **not** expire or
reset on `healthy`, process restart, clock changes, rule changes, or temporary
disappearance of a container. This deliberately trades unattended recovery from
later incidents for a hard bound on repeated attempts. After diagnosing and
correcting the problem, rearm the exact container using its full Docker ID:

```sh
monit-docker restart-reset \
  --state-file /var/lib/monit-docker/health.json \
  --container-id FULL_64_CHARACTER_CONTAINER_ID
```

Use the same state file and audit configuration as the monitoring job. The reset
requires the state lock, records an operator audit event and resets only this
container's restart counter. Existing cooldowns, trigger observations and other
containers' counters remain intact. It does not contact Docker or restart the
container. An unknown ID with no recorded attempts returns code 110; contention
returns 117; state failures return 118 and journal failures return 119.

With [authenticated UI actions](ui.md) enabled, **Rearm auto restarts** provides
the same reset for a selected container with a nonzero counter. It requires
confirmation and respects container protection and the manual action cooldown.
The queued request is audited, and selection and protection are checked again
before execution. Its result appears in Manual activity. Neither reset method
restarts the container itself; matching automatic rules may act on the next
cycle once their cooldown and trigger requirements permit.

Keep the state file on persistent storage. Files from schema versions 1 and 2
are upgraded to version 3 on the first restart reservation, preserving their
existing entries. Older monit-docker versions cannot read version 3. Deleting
state, using another state file, raising the limit, or recreating a container
with a new ID provides a fresh or larger budget; no host-wide coordination
across separate state files is implied. Retired IDs are retained until explicitly
reset; counters must not disappear just because selection temporarily changes.

The limit covers Docker `restart` actions issued by these automatic modes. An
explicit one-shot `monit --cmd restart` or authenticated manual UI action remains
an operator operation, and does not reset the automatic budget. Docker's own
restart policy, other controllers, `stop`/`start` sequences and arbitrary exec
commands are outside this guard. Schedule `cron`, not one-shot `monit`, to use
the persistent protection.

`--dry-run` models reservations within the current cycle without changing the
state file or executing commands; its snapshots show the simulated budget.

## Observe the limit

In `serve` with rules, each `/v1/status` container includes `restart_attempts` and
`restart_limit`. The UI shows the counter and marks an exhausted budget as
blocked. A healthy container can still have an exhausted budget: current health
and permission to attempt another automatic restart are independent.

Prometheus exposes these gauges, labelled by container `id` and `name`:

- `monit_docker_container_restart_attempts`: attempts reserved since rearm.
- `monit_docker_container_restart_limit`: configured limit.
- `monit_docker_container_restart_blocked`: 1 when the budget is exhausted, 0 otherwise.

These are gauges because explicit rearm can lower the count. They are omitted
when no restart policy is active or collection data is unavailable/stale.
`monit_docker_action_decisions_total{outcome="restart-limit"}` counts skipped
actions since service startup, including whole sequences that do not fit the
remaining budget. It is not a count of currently blocked containers.

An optional alert uses the existing notification pipeline:

```yaml
- alert: MonitDockerRestartLimitReached
  expr: |
    (monit_docker_container_restart_blocked{job="monit-docker"} == 1)
    and on (job, instance) (monit_docker_ready{job="monit-docker"} == 1)
    and on (job, instance) (up{job="monit-docker"} == 1)
  labels:
    severity: warning
  annotations:
    summary: 'Automatic restart budget exhausted for {{ $labels.name }}'
```
