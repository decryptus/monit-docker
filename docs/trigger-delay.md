# Wait for a sustained condition before acting

> Unreleased: these options require a build newer than v0.0.61.

Use `--trigger-after` with `cron` or `serve` to wait until a condition has been
observed true for a minimum duration before running its actions. The tracking
uses the existing local state file, so cron requires no server or Redis.

These mechanisms serve different purposes:

| Mechanism | Meaning | Where it runs |
| --- | --- | --- |
| `--trigger-after 300 --max-gap 120` | Wait for true observations spanning five minutes, with at most two minutes between observations | The local agent, before conditional actions |
| `--cooldown 300` | Space attempts of a rule by at least five minutes, including failed attempts | The local agent, after claiming an action sequence |
| Prometheus `for: 5m` | Wait five minutes before a matching alert becomes firing | Prometheus; already provided by the [alert examples](alerts.md) |

The local delay is optional and defaults to **0** (immediate). It applies to all
conditional rules in this invocation. An unconditional command such as
`--cmd restart` still runs immediately, subject to cooldown. For different delays
per rule, use separate jobs with separate state files. `monit` retains its simple
immediate behavior.

## Cron example

Run once per minute:

```text
* * * * * /usr/local/bin/monit-docker --name 'web*' cron --state-file /var/lib/monit-docker/web.json --trigger-after 300 --max-gap 120 --cooldown 600 --cmd-if 'mem_percent > 90 ? restart'
```

The first high measurement starts the timer. If every following measurement is
also high and no observation gap exceeds 120 seconds, a measurement at or after
five minutes allows a restart. The agent checks at each invocation; it never
sleeps to wait for the threshold. Scheduling and collection time affect when
observations arrive. It cannot know what happened between measurements.

## Serve example

```sh
monit-docker --name 'web*' serve --interval 30 \
  --state-file /var/lib/monit-docker/web.json \
  --trigger-after 300 --max-gap 120 --cooldown 600 \
  --cmd-if 'mem_percent > 90 ? restart'
```

Both commands share the same tracking format and identities. Switching between
them preserves a streak if the rule, timing options, container ID and state path
are unchanged, and the observation gap remains within the limit. A process
restart alone does not reset a streak; a long interruption does.

`--trigger-after` must be finite and non-negative. When positive, it requires an
explicit finite positive `--max-gap`. Choose that gap to cover the normal
schedule **plus collection time**. In `serve`, it must exceed `--interval`, which
is the wait *after* each cycle. `--max-gap` without a positive delay is an error.
Invalid options fail before Docker is contacted.

## Reset and retry behavior

Tracking is per resolved rule and full container ID. All conditions in a rule
must match. A renamed container keeps its identity; a replacement container or
changed resolved rule starts a new timer. Changing either timing option also
starts a new timer, while preserving any existing cooldown for the same rule.

A streak resets when:

- A condition is observed false, including a return to normal resource usage.
- A rule cannot be evaluated in a cycle: for example, its container is unselected,
  stopped while the rule requires metrics, or collection fails before evaluation.
- The time between true observations exceeds `--max-gap` (equality is allowed).
- The wall clock moves backward past the previous observation.

At cycle entry, the agent durably clears previous observations, retaining a copy
in memory to compare against new measurements. It writes back only observations
actually obtained. A crash during collection therefore cannot preserve an old,
unobserved streak. Earlier valid observations in a partially failed cycle remain
valid; observations are tracked per rule/container, not as one cycle transaction.
Lock contention does not write state; the next cycle still checks the gap.

Once the condition has lasted long enough, the normal cooldown decides whether
actions may run. A continuing condition stays eligible after the cooldown expires;
it does **not** need a fresh full delay after each attempt. A false condition
resets its duration, but never clears its action cooldown. With cooldown zero,
an eligible condition can cause actions in every cycle. Failed action sequences
retain their cooldown, as described in [cron](cron.md).

Times are Unix wall-clock seconds so they survive separate processes and reboots.
A forward clock adjustment within the allowed gap can advance eligibility; a
larger jump resets the streak. This is observation-based duration tracking, not
proof of uninterrupted condition truth or a hard real-time guarantee.

## Output, preview and storage

A matching conditional rule whose delay has not elapsed reports `pending`, once
per action in that rule. It neither executes actions nor reserves a cooldown.
Cron writes the same JSON decision format as usual:

```json
{"container_id":"abc123","rule":"mem_percent > 90 ? restart","command":"restart","status":"pending"}
```

Serve counts these decisions in `/v1/status` under `actions.pending`, and in
`monit_docker_action_decisions_total{outcome="pending"}`. This is a cumulative
counter of skipped action decisions, **not** the number of currently pending
rules or their remaining delay. Nonmatching rules report no decision.

`--dry-run` evaluates the existing stored observations, but changes no JSON file
and performs no action. It can report `pending`, `cooldown` or `dry-run`. It does
not accumulate a new persistent streak across previews. In particular, repeated
serve preview cycles without prior real observations remain pending. Directory
and lock-file creation have the same semantics as the existing cron preview.

The first persisted observation upgrades the state to **schema version 2**,
which stores `observations` alongside `cooldowns`. Existing version 1 cooldowns
are read and preserved. Jobs that never use the delay continue writing version 1.
Disabling the delay clears its tracking on the next real cycle and preserves
cooldowns; a migrated file remains version 2. Older monit-docker versions reject
version 2 with exit **118**. Back up the state before upgrading if rollback is
needed; restoring an old backup also restores its old cooldown reservations.

The existing process lock, private files and atomic, synced writes are reused.
Corrupt state or a failed state write returns **118** and prevents the affected
action. Store the entire state directory persistently, one file per job and
Docker host. Measurements and Prometheus history are still not stored there.
