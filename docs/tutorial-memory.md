# Reproduce a memory incident with Compose

This local lab uses a disposable Python worker with a bounded cache. It simulates
retained application data, not a specific software leak. It starts in observation
mode, then explicitly enables one automatic restart. No notification service is
configured. Use a Linux amd64 lab host with the [Compose prerequisites](compose-quickstart.md).

## Start the lab

From the repository root, prepare the UI credentials once if not already present:

```sh
python3 examples/ui/prepare.py --self-signed
```

Use this POSIX shell function in the same terminal throughout the lab:

```sh
dc() {
  docker compose -f docker-compose.yml -f docker-compose.ui.yml \
    -f examples/tutorial-memory/compose.yaml "$@"
}
export DEMO_SCENARIO=demo-observe
dc config --quiet
dc up -d --wait --wait-timeout 180
dc exec monit-docker monit-docker -c /etc/monit-docker/tutorial.yml check-config
```

Open **https://localhost:8443**. The agent selects only the worker that has both
the exact demo name and a matching group label value. Existing containers are
not rule targets. The worker has 512 MiB of memory, a 0.5 CPU limit, no network
and no Docker restart policy. It starts without growing its cache. A name collision
fails creation; do not remove an unrelated existing container to make room.

The tutorial overlay replaces the agent command and mounts its own YAML. The
normal root installation continues to observe all containers when this overlay
is absent. Stop the root stack before moving to a separate project/port to avoid
running multiple agents unintentionally.

`dc restart ui` below refreshes the proxy after the agent is recreated.
For the lab without a browser, skip those restart commands, omit `-f docker-compose.ui.yml` from `dc` and skip
the UI preparation. Use the terminal commands below instead.

## Observe and preview

Record the worker's original start time, then trigger controlled growth:

```sh
dc exec monit-docker monit-docker --name monit-docker-tutorial-memory stats --output json
docker inspect --format '{{.State.StartedAt}}' monit-docker-tutorial-memory
dc kill --signal SIGUSR1 demo-worker
```

`kill --signal SIGUSR1` sends a signal handled by the demo script; it does not stop
this worker. It adds 12 MiB per second, up to 360 MiB, then stays at that plateau.
The reported memory also includes the interpreter and runtime overhead. Allow
about 30 seconds for the plateau, then refresh the UI and observe real values.

```sh
dc exec monit-docker monit-docker --name monit-docker-tutorial-memory \
  monit --dry-run --cmd-if 'mem_percent > 60 ? restart'
docker inspect --format '{{.State.StartedAt}}' monit-docker-tutorial-memory
```

The dry run reports the matching action without performing it. The start time
must remain unchanged. This one-shot preview demonstrates the selection/threshold;
it does not exercise the sustained timer or build durable observations. The
automatic configuration below supplies those policies.

## Read the YAML and enable the guard

Open `examples/tutorial-memory/monit-docker.yml`. The `tutorial` group matches
label **values**, not Docker's `key=value` filter syntax. The explicit name
intersects that group. `demo-observe` has no action rule. `demo-guard` uses:

| Setting | Lab value | Meaning |
| --- | --- | --- |
| Memory condition | `mem_percent > 60` | Compared with the container memory limit |
| Interval | 5 seconds | Wait after each completed cycle |
| Trigger delay | 20 seconds | Require high observations spanning at least this duration |
| Maximum gap | 15 seconds | Reset the streak if observations are too far apart |
| Cooldown | 120 seconds | Space attempts of this rule |
| Restart budget | 1 | One automatic restart attempt for this container ID and state file |

These short timings are for filming. Choose application-specific thresholds and
longer observation windows for production. A restart is only appropriate when
the workload can tolerate it; it does not diagnose or repair a memory leak.

```sh
export DEMO_SCENARIO=demo-guard
dc up -d --no-deps --wait --wait-timeout 180 monit-docker
dc restart ui
dc logs --follow --tail=20 monit-docker
```

Keep the UI open and refresh the measurements. The first high sample starts the
timer. An eligible later cycle attempts the restart. Collection time affects the
delay, so do not promise an exact twenty-second response. Stop following logs
with Ctrl+C; the agent continues running in the background.

```sh
docker inspect --format '{{.State.StartedAt}}' monit-docker-tutorial-memory
dc exec monit-docker monit-docker --name monit-docker-tutorial-memory stats --output json
dc logs --tail=12 demo-worker
```

The start time changes, memory drops and the worker emits new ticks. Inspect the
journal for the recorded action result. A successful Docker restart alone does
not prove a real application's health; this worker's log is only a lab liveness check.

## Read the journal and terminal interface

```sh
umask 077
dc exec -T monit-docker monit-docker \
  --audit-file /var/lib/monit-docker/audit/events.jsonl \
  audit-export --format csv > tutorial-actions.csv
dc exec -T monit-docker monit-docker \
  --audit-file /var/lib/monit-docker/audit/events.jsonl \
  audit-export > tutorial-events.jsonl
```

Check command exit status before using an export; a write/read failure may leave
a partial file. These are event histories, not time-series metric archives.
Optional interactive view from a real terminal:

```sh
dc exec -e TERM=xterm-256color monit-docker monit-docker \
  --name monit-docker-tutorial-memory \
  --audit-file /var/lib/monit-docker/audit/events.jsonl tui --refresh 5
```

The image includes the curses interface. Use Tab for the journal, Enter for
details, and q to quit. The TUI is read-only and does not run action rules. The
[Textual interface](textual.md) is an optional Python extra, not assumed to be
installed in the standard Docker image. Do not substitute synthetic demo screens
for the lab's live measurements.

## Stop, repeat or return to the minimal installation

Disable the automatic scenario before another take:

```sh
export DEMO_SCENARIO=demo-observe
dc up -d --no-deps --wait --wait-timeout 180 monit-docker
dc restart ui
dc kill --signal SIGUSR2 demo-worker
```

For the same worker ID, the restart budget stays consumed after recovery. To
rearm deliberately for another take, keep observation mode active, obtain the
full ID and reset only that ID:

```sh
DEMO_CONTAINER_ID=$(docker inspect --format '{{.Id}}' monit-docker-tutorial-memory)
dc exec monit-docker monit-docker \
  --audit-file /var/lib/monit-docker/audit/events.jsonl restart-reset \
  --state-file /var/lib/monit-docker/tutorial/state.json \
  --container-id "$DEMO_CONTAINER_ID"
```

Run that reset only after an attempt has been recorded. It preserves cooldowns;
wait until the original 120-second cooldown has elapsed before filming a new
automatic action. Do not delete the state file to hide or bypass the budget.

```sh
dc down
unset DEMO_SCENARIO
```

This removes the lab containers and networks but keeps state and UI credentials.
For agent-only, now run `docker compose up -d --wait`. If the worker is recreated
it gets a new container ID and therefore a fresh budget; the old journal remains.
Archive the exported files before another session. Prometheus/Grafana and external
notifications are optional follow-on exercises, not requirements for this lab.
