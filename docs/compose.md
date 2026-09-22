# Serve, Prometheus and Grafana with Docker Compose

This optional example starts the complete **serve mode** stack and loads the
dashboard automatically. It observes real containers on your Docker daemon and
executes no remediation rules. For a command that exits after one cycle, use
[simple mode](simple.md); none of this stack is required for it.

## Start

Use Docker Engine with Linux containers, a local Unix Docker socket, and a recent
Docker Compose v2 with `up --wait` support. Docker Desktop with Linux containers
can also be used, although this example is tested in CI on Linux Docker Engine.
The account running these commands must have Docker access. Git and a POSIX shell
are used below; Windows users can run the commands in WSL with Docker integration.

```sh
git clone https://github.com/decryptus/monit-docker.git
cd monit-docker/examples/monitoring
sh start.sh
```

The first run downloads the pinned images and generates a random Grafana admin
password in `.env`, with permissions 0600. Subsequent runs reuse that file.
The script waits for healthy services; startup may take several minutes. It does
not print the password. In your local terminal, read it with:

```sh
cat .env
```

Open **http://127.0.0.1:3000**, log in as **admin** with that password, and open
the **monit-docker — Overview** dashboard in the **monit-docker** folder. It is
also configured as the home dashboard. Prometheus is already connected; no JSON
import or data-source setup is needed.

The dashboard shows the host's containers, including the monitoring stack itself.
Allow roughly one to two minutes for several scrapes before interpreting rate
panels. The [gallery](grafana.md) illustrates the layout with synthetic data;
this Compose example uses your actual measurements.

Three alert rules are loaded automatically. Open
**http://127.0.0.1:9090/alerts** to see agent availability, collection readiness
and high container memory alerts. See the [alert guide](alerts.md) for thresholds,
diagnosis and customization. This example sends no outbound notifications.
To opt in to email, Slack or both, use the [notification guide](notifications.md)
and `sh start.sh --notifications` after configuring your receivers.

## What runs and where data lives

| Service | Local address | Stored data |
| --- | --- | --- |
| monit-docker `serve` | http://127.0.0.1:9808 | Latest completed cycle in memory; cleared on restart |
| Prometheus | http://127.0.0.1:9090 | Time-series history in the `prometheus-data` volume; retention 15 days |
| Grafana | http://127.0.0.1:3000 | Users, preferences and saved dashboards in the `grafana-data` volume |

All published ports bind to host loopback. Containers communicate over their
Compose network: Prometheus scrapes `monit-docker:9808`, and Grafana queries
`prometheus:9090`. Changing a host port does not change these internal addresses.

Only the agent receives the Docker socket. Access to that socket grants control
of the Docker daemon even when the mount has `read_only: true`; that mount flag
does not make the Docker API read-only. The example runs no action rules.

Prometheus and Grafana add their own resource usage. They are optional external
services and do not become dependencies of the lightweight agent.

## Check the first measurements

Run from `examples/monitoring`:

```sh
docker compose ps
curl -i http://127.0.0.1:9808/readyz
curl http://127.0.0.1:9808/v1/status
```

`/readyz` should return 200. In Prometheus, query
`up{job="monit-docker"}` and `monit_docker_ready{job="monit-docker"}`; both
should be 1. `up` checks scraping, while `monit_docker_ready` checks fresh,
successful collection. Read the [metrics reference](metrics.md) for the full list.

If startup fails, inspect `docker compose ps` and
`docker compose logs --tail=100 monit-docker prometheus grafana`. The script
leaves the containers available for diagnosis. An unhealthy agent is not
automatically restarted solely because its health check fails; it retries
collection itself. See [troubleshooting](troubleshooting.md).

## Ports, credentials and the Docker socket

Keep the generated password and optionally append these settings to `.env`:

```sh
MONIT_DOCKER_PORT=19808
PROMETHEUS_PORT=19090
GRAFANA_PORT=13000
DOCKER_SOCKET_PATH=/var/run/docker.sock
```

Run `sh start.sh` again after changes. Use the corresponding new port in your
browser and `curl` commands. If you use rootless Docker or a non-default local
socket, set `DOCKER_SOCKET_PATH` to its absolute path. A missing socket fails
startup instead of silently creating a directory. Remote Docker contexts and
Windows container daemons are outside this example's scope.

Keep `.env` private; it is ignored by Git and excluded from source packages.
Grafana uses the environment password when initializing a new database. Editing
`.env` does **not** rotate a password already stored in Grafana: change it through
Grafana's account settings and keep your stored credentials consistent. Do not
delete `.env` merely to change a password when reusing the Grafana volume.

To manage credentials yourself, create `.env` containing a nonempty
`GRAFANA_ADMIN_PASSWORD` before the first launch, then run `sh start.sh`. Without
the script, the equivalent launch command is
`docker compose up -d --wait --wait-timeout 240` from this directory.

## Stop, restart and remove data

Stop the stack while keeping its history and Grafana database:

```sh
docker compose down
```

Restart it with `sh start.sh`. Keep the same directory/project name so Compose
reattaches the same volumes. Changing the project name creates a separate stack
and separate volumes.

Only when you intend to **delete this stack's Prometheus history and Grafana
database**, run:

```sh
docker compose down --volumes
```

The generated `.env` remains on disk. Named volumes provide persistence, not
backups; archive them separately if you rely on this history.

## Customize

The services are pinned to monit-docker 0.0.57, Prometheus 3.5.0 and Grafana 12.2.0.
Update versions deliberately in `examples/monitoring/compose.yaml`, then run
`docker compose pull` and `sh start.sh`. The repository-root `docker-compose.yml`
is the older cron example; use the file in `examples/monitoring` for this stack.

The dashboard is mounted directly from `examples/grafana/monit-docker.json`.
Provisioned panels cannot be saved over from the Grafana UI; save a copy under a
different name/UID to customize it, or edit the source JSON. The single source
file remains the one validated by the dashboard tests.

To enable actions later, follow [serve's optional remediation guide](serve.md#optional-remediation):
add explicit rules, a persistent state-directory mount and `--state-file`, and
start with `--dry-run`. Prometheus alerts only report conditions; they do not
execute agent actions. Notification delivery requires a separately configured
Alertmanager; [email and Slack examples](notifications.md) are included.
