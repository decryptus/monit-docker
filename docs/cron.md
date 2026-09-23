# One-shot monitoring from cron

This is an optional part of [simple mode](simple.md): the operating system's cron
repeats a command that runs once and exits. For a process that keeps monitoring
and exposes HTTP endpoints, use [serve mode](serve.md).

`monit-docker cron` runs one monitoring cycle and exits. It uses the same engine,
selectors and rule syntax as `monit`, with a process lock and persistent cooldowns.
It starts no server, requires no account and adds no runtime dependencies.
Existing `monit` and `stats` behavior is unchanged unless `--dry-run` is requested.

## Try a rule safely

```sh
monit-docker --name 'web*' cron \
  --state-file /var/lib/monit-docker/web.json \
  --cooldown 300 \
  --dry-run \
  --cmd-if 'mem_percent > 90 ? restart'
```

Global options (configuration, client, selectors, log/runtime paths) precede
`cron`; cron options follow it. At least one `--cmd` or `--cmd-if` is required.
The state path is mandatory so each job has an explicit identity. The directory
must be writable by the cron user, who also needs Docker access. New directories
are created with mode 0700, and new state and lock files with mode 0600.

Dry run collects real container data and evaluates the rules. It emits one JSON
line per matching action, with status `dry-run`, `cooldown` or `pending`, and never calls
the action executor. It does not create or change the JSON state file; it may
create the directory and lock file needed to obtain a consistent state snapshot.
Reservations are simulated in memory to handle duplicate rules within the cycle.
It cannot predict state changes that real actions would cause, or whether a
command would succeed. Nonmatching rules produce no action line.

For a preview without locking or cooldowns, the legacy command also accepts:

```sh
monit-docker --name 'web*' monit --dry-run --cmd-if 'mem_percent > 90 ? restart'
```

## Schedule the job

Remove `--dry-run` after reviewing the decisions. For example, run every minute
with five minutes between attempts of the same rule on the same container:

```text
* * * * * /usr/local/bin/monit-docker --name 'web*' cron --state-file /var/lib/monit-docker/web.json --cooldown 300 --cmd-if 'mem_percent > 90 ? restart'
```

Adjust the executable path to your installation. Cron must have the required
configuration and Docker environment variables; it does not inherit your
interactive shell environment. Normal execution emits JSON action decisions
with status `executed`, `cooldown` or `pending`. Existing diagnostic logging goes to stderr
and the configured log file. Cron may mail the output according to local settings.

Example decision:

```json
{"container_id":"abc123","rule":"mem_percent > 90 ? restart","command":"restart","status":"cooldown"}
```

For a delay **before** the first action, use the optional
[`--trigger-after` and `--max-gap` options](trigger-delay.md). Without them, the
first matching rule can execute immediately. The existing cooldown then spaces
attempts.

## Locking and persistent state

The process acquires a nonblocking Unix advisory lock on `<state-file>.lock`
before opening a Docker connection and holds it until the cycle and cleanup end.
Processes using the same state path cannot overlap. A busy lock returns **117**
immediately; it does not wait. The operating system releases the lock on process
exit, including an abrupt exit. The lock file remains: do not delete it to unlock
a job, because that could let a second process acquire a different lock inode.

For Python integrations, a `LocalState` instance must not be entered twice at the
same time or reused for writes in a child after `fork`. Both cases are rejected;
create a fresh instance in each process. Cooldown keys and timestamps are checked
before any state change, including dry runs.

Use one state file per job and Docker host, on a local filesystem supporting
`flock`, atomic rename and `fsync`. Copies of the same job must share that path;
different paths do not coordinate. For containerized invocations, mount the
entire state directory persistently, including the lock file. Keep it in a
trusted directory; symlinks for the state or lock file are rejected on Linux.

The cooldown defaults to **300 seconds** and applies to a complete rule's action
sequence for one container ID. Its identity includes resolved conditions and all
actions, including arguments and options. Changing an alias's behavior or
replacing a container gives a fresh identity. Renaming the same container does
not reset its cooldown. Matching the same rule twice shares the cooldown.
Rule arguments must be JSON-compatible values; unsupported values are rejected
before connecting to Docker.

Before the first action of an eligible rule, its next eligible timestamp is
written to a temporary file, synced, atomically renamed and the directory synced.
Only then can the actions execute. If any action fails, later actions and
containers stop as before, but the whole rule retains its cooldown. A following
cycle does not immediately retry or resume the middle of that action sequence.
Other rules have independent cooldowns.

This is a bound on attempts, not an exactly-once guarantee: a crash between
reservation and execution can defer a rule that never ran. After the cooldown,
the complete rule can run again if it still matches. Time is wall-clock time so
it survives process restarts; moving the clock backward delays eligibility and
moving it forward may expire cooldowns earlier in elapsed time.

`--cooldown 0` creates no new delay. Existing unexpired reservations are still
respected, even if the configured duration is reduced. Expired entries are pruned
when a new reservation is saved. Keep the state across reboots; do not use `/run`
or a temporary directory if cooldowns must survive a reboot.

Invalid, unsupported or unreadable state returns **118** before any Docker
connection. A failed state write also returns **118** and prevents that rule's
actions. Earlier actions in the cycle are not rolled back. Corrupt state is never
silently reset. To recover, stop the scheduled job, inspect and back up its state,
then restore a valid copy or deliberately remove the JSON file to reset delays.
Leave the lock file in place.

## Exit codes and limits

| Code | Meaning |
| --- | --- |
| 0 | Cycle completed, including a preview or rules skipped by cooldown or trigger delay |
| 2 | Invalid CLI arguments |
| 110 | Invalid configuration or action |
| 114 | No matching container |
| 115 | Incomplete statistics |
| 116 | Action failed (existing default) |
| 117 | Another process holds this job's lock |
| 118 | State cannot be safely loaded or saved |
| 170 / 180 | Existing Docker connection / API errors |

`--propagate-exit-code` is also supported by `cron`: a failed synchronous exec
can return its own code instead of 116, including values that overlap agent
codes. Check diagnostics to distinguish them. Other existing exit codes retain
their meaning.

There is no background scheduler or HTTP listener. The optional [trigger delay](trigger-delay.md) tracks sustained conditions.
There is no cycle-wide deadline or hysteresis. Existing Docker client timeouts still apply; a hung cycle can hold its
lock until it exits or is terminated. [Serve mode](serve.md) reuses the same
engine and cooldown policy for continuous monitoring.
