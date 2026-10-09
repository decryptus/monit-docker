# Install with Docker Compose

[Version française](compose-quickstart-fr.md)

Choose the minimal agent, add its web UI, or use the separate Prometheus/Grafana
stack. None of these installations enables automatic container actions by default.

| Need | Compose files | Local address |
| --- | --- | --- |
| Agent and API | `docker-compose.yml` | `http://127.0.0.1:9808` |
| Agent and web UI | Base plus `docker-compose.ui.yml` | `https://localhost:8443` |
| Metrics history and dashboards | `examples/monitoring/compose.yaml` | Grafana on `http://127.0.0.1:3000` |

## Prerequisites

Use Linux Docker Engine, a local Unix Docker socket and a current Docker Compose
plugin with `up --wait` support. The account needs Docker access. The published
agent/UI images target Linux amd64; other architectures need a separately tested
build or emulation. Docker Desktop must use Linux containers; for Windows commands
use WSL with Docker integration. The UI preparation also needs Python 3 and OpenSSL.

```sh
git clone https://github.com/decryptus/monit-docker.git
cd monit-docker
docker compose version
docker info
```

These commands use the default local socket. For rootless Docker or another local
socket, set `DOCKER_SOCKET_PATH` to its absolute path. A missing path fails instead
of creating a directory. Remote Docker contexts are outside this quickstart.

## Start the agent

From the repository root:

```sh
docker compose config --quiet
docker compose up -d --wait --wait-timeout 180
curl -f http://127.0.0.1:9808/readyz
curl -f http://127.0.0.1:9808/v1/status
```

The agent observes containers every 30 seconds after each completed cycle. It
does not restart workloads. `/readyz` returns 200 only after a successful fresh
collection. `/healthz` only checks that HTTP responds. For diagnosis:

```sh
docker compose ps
docker compose logs --tail=100 monit-docker
docker compose exec monit-docker monit-docker stats --output json
```

Only the agent receives the Docker socket, and the host API port is on loopback.
A socket bind marked `read_only` still grants Docker API control; it is not a
read-only authorization boundary. Keep the daemon and the agent trusted.

## Add the web UI

Prepare credentials once. Enter a username and a password of at least 12
characters; the password is not printed or passed as a process argument.

```sh
python3 examples/ui/prepare.py --self-signed
docker compose -f docker-compose.yml -f docker-compose.ui.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.ui.yml up -d --wait --wait-timeout 180
```

Open **https://localhost:8443** and log in with those credentials. The generated
certificate is for local development and expires in seven days. For this local
example the browser reports it as untrusted. Use your trusted certificate for a
real deployment, following the [UI guide](ui.md). The preparation command refuses
to overwrite existing `examples/ui/secrets.local`; reuse those files on later runs.

The overlay reuses the existing UI service and its secret paths. It adds no
Prometheus or Grafana, and enables no manual start/stop/restart controls. The
loopback-only agent API remains available. Only the UI joins the frontend network.
Do not combine the advanced `examples/ui/compose.actions.yaml` or journal overlay
with the root files: follow their standalone instructions and relative paths instead.

Keep the same Compose file list for all UI lifecycle commands:

```sh
docker compose -f docker-compose.yml -f docker-compose.ui.yml ps
docker compose -f docker-compose.yml -f docker-compose.ui.yml logs --tail=100
docker compose -f docker-compose.yml -f docker-compose.ui.yml down
```

`down` preserves the named state volume and the credential files. Do not use
`--volumes` when you need to preserve journals, restart budgets or other state.
To return from the UI to agent-only, stop with both files, then start the base file.

## Customize or update

Set `MONIT_DOCKER_PORT` or `UI_PORT` in the shell before running Compose to change
host ports. The UI binds to loopback unless `UI_BIND` is explicitly changed.
Changing a host port does not change internal agent addresses. Keep the same
directory/project name so Compose reuses its named volume.

The examples pin agent and UI to **1.1.1**. When updating versions, preserve your
configuration and state, choose matching agent/UI versions, then pull and recreate
the selected stack. `docker compose pull` alone does not change a running container.

```sh
docker compose -f docker-compose.yml -f docker-compose.ui.yml pull
docker compose -f docker-compose.yml -f docker-compose.ui.yml up -d --wait --wait-timeout 180
```

For agent-only use the same commands without `-f` arguments. See
[installation and upgrades](installation-upgrades.md) for state compatibility.

## Existing root cron installations

The previous root file used `latest`, mounted all of `/var/run`, and ran broad
cron rules including PHP-FPM signals. The new root file intentionally starts an
observation-only server. **It does not execute the old `MONIT_DOCKER_CRONS` jobs.**

Before pulling this change into an existing deployment, save its Compose file,
configuration, state and audit files outside the checkout. Stop the old stack
using that saved file and its original project name. Review each old rule and its
container selection; follow the [cron guide](cron.md) to retain scheduled jobs,
including persistent state and an initial dry run. There is no automatic rule
conversion. Then start the new entry point deliberately. Do not run both old and
new action jobs over the same workloads without reviewing their coordination.

## Try a reproducible incident

The [memory tutorial](tutorial-memory.md) adds a disposable worker, limits its
memory, and then opts into one automatic restart after a sustained threshold.
Use it for a local walkthrough before writing rules for your applications.

## Add history later

Use the [Prometheus/Grafana Compose guide](compose.md) when you need time-series
history. That example is a separate stack, not another overlay on the root file.
Stop the minimal stack first, or set distinct host ports. Email/Slack delivery
requires the explicitly configured [notification stack](notifications.md).
