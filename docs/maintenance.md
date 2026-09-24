# Temporary maintenance (since 0.0.69)

Maintenance pauses **this agent's automatic rule actions** for one exact Docker
container ID. Measurements, metrics and the audit journal continue. Manual
start/stop/restart and explicit CLI `monit` commands remain available. Docker's
own restart policy, the public demo's host recovery timer, other agents and
external Prometheus/Alertmanager notifications are unaffected.

## CLI: finite pause and early resume

Use the same state file as `cron` or `serve` and the full 64-character ID:

```sh
monit-docker maintenance --state-file /var/lib/monit-docker/state.json \
  --container-id FULL_CONTAINER_ID --duration 900
monit-docker maintenance --state-file /var/lib/monit-docker/state.json \
  --container-id FULL_CONTAINER_ID --duration 0
```

Duration is an integer from 1 to 86400 seconds; 0 resumes early. The command
works offline without Docker access and does not resolve names. A replacement
container has a new ID and does not inherit the pause. Overlapping cycles or
administrative commands fail with the existing state-lock error; retry after
the operation finishes. No running action is interrupted retroactively.

## Optional interface controls

Use matching agent and UI images tagged **0.0.69 or newer**.
Add `--allow-maintenance` to an existing `serve --allow-actions` deployment.
The flag requires the existing action token, exact browser origin and state
file. The existing `/v1/actions` API accepts `maintenance-15m`,
`maintenance-1h` and `maintenance-off`, with the same request ID and container
ID fields as other actions. Controls are absent and requests rejected unless
explicitly enabled. Container protection still blocks these manual controls;
an administrator with state-file access can use the CLI instead.

The UI shows the expiry time and asks for confirmation. Requests are serialized
with monitoring, recheck the selected container and its protection immediately
before execution, and keep the usual manual cooldown. The journal records the
request, actor and result. Automatic skips have reason `maintenance`; the first
cycle after expiry records `maintenance-expired`.

## Persistence and resumption

The deadline is stored atomically in local state schema 4 and survives an agent
restart. Existing schemas 1–3 migrate on the first maintenance change; older
agents reject schema 4 rather than silently ignoring a pause. Preserve a backup
before downgrading. Deadlines use UTC Unix time: keep the host clock synchronized.
Resumption occurs when the scheduler next evaluates rules, not through another
timer or service. If the agent is stopped, it cannot execute actions.

Maintenance never consumes automatic cooldowns or restart attempts. Existing
cooldowns and restart budgets still apply after resumption. For conservative
trigger timing, entering, leaving or expiring maintenance clears sustained
condition observations in the shared state file, including those for other
containers; `--trigger-after` must build a fresh observation streak.

Status snapshots expose `maintenance_active` and `maintenance_until` as observed
at collection time. Prometheus exposes `monit_docker_container_maintenance_active`
and `monit_docker_container_maintenance_until_seconds`; stale samples are omitted.
A dry run reports decisions without changing the state file.
