# monit-docker project

<p align="center">
  <img src="https://raw.githubusercontent.com/decryptus/monit-docker/v0.0.63/ui/branding/monit-docker-logo.png" alt="monit-docker logo" width="640">
</p>

[![PyPI pyversions](https://img.shields.io/pypi/pyversions/monit-docker.svg)](https://pypi.org/project/monit-docker/)
[![PyPI version shields.io](https://img.shields.io/pypi/v/monit-docker.svg)](https://pypi.org/project/monit-docker/)
[![Docker Cloud Build Status](https://img.shields.io/docker/cloud/build/decryptus/monit-docker)](https://hub.docker.com/r/decryptus/monit-docker)
[![Documentation Status](https://readthedocs.org/projects/monit-docker/badge/?version=latest)](https://monit-docker.readthedocs.io/)

[Website](https://www.monit-docker.com/) ·
[Documentation (FR)](https://www.monit-docker.com/docs/fr/) ·
[Documentation (EN)](https://www.monit-docker.com/docs/en/) ·
[Live demo](https://demo.monit-docker.com/)

For persistent action and notification history, available since **0.0.65**, see
the [audit journal guide](docs/audit.md). Since **0.0.66**, it also includes an optional
authenticated journal page with filters, bounded reads and page exports.

monit-docker is a free, open-source tool for checking Docker containers and
optionally taking action when a condition matches, such as restarting a stopped
container or reloading PHP-FPM when memory usage is high.

**Choose one of two modes.** Both use the same container selectors and rule syntax.

| Mode | Use it for | How it runs | What you need |
| --- | --- | --- | --- |
| **Simple** | Read statistics, check a status, or execute a rule | One command, then exit; optionally repeat with cron | Docker access and monit-docker |
| **Serve** | Monitor continuously and expose status and metrics over HTTP | A process that stays running | Docker access and monit-docker; optionally Prometheus for history and Grafana for charts |

The simple mode remains a complete way to use the tool. It requires no HTTP
server, Prometheus or Grafana. Its commands are `stats`, `monit`, and optionally
`cron` when you need locking and persistent cooldowns between actions.

## Table of contents

1. [Installation](#installation)
2. [Quickstart: simple or serve](#quickstart)
3. [Simple mode guide](docs/simple.md)
   - [Wait for sustained conditions before acting](docs/trigger-delay.md)
   - [Disk space, inodes and ro/rw mounts with reusable directory groups](docs/filesystems.md)
   - [File and directory access, including POSIX ACLs](docs/access-checks.md) (source tree)
   - [Docker healthchecks and unhealthy-container rules](docs/healthchecks.md)
   - [OOM events, repeated starts and process/thread checks](docs/runtime-checks.md)
   - [Bound automatic restarts and explicitly rearm](docs/restart-limit.md)
   - [Temporarily pause automatic actions for maintenance](docs/maintenance.md) (source tree)
4. [Serve mode guide](docs/serve.md)
   - [Web interface previews](#optional-web-interface)
   - [Optional mobile-friendly interface with Nginx](docs/ui.md)
   - [Ready-to-run Docker Compose stack](docs/compose.md)
   - [Prometheus alerts and thresholds](docs/alerts.md)
   - [Email and Slack notification examples](docs/notifications.md)
   - [Alertmanager to Redis Streams](docs/redis-notifications.md)
   - [Custom HTTP notifications with DWho](docs/dwho-http-notifications.md)
5. [Prometheus metrics](docs/metrics.md) and [Grafana dashboard](docs/grafana.md)
6. [Troubleshooting](docs/troubleshooting.md)
7. [Environment variables](#environment_variables)
8. [Sub-command: monit](#sub-command_monit)
    1. [Basic commands](#monit_basic_commands)
    2. [Advanced commands](#monit_advanced_commands)
    3. [Container informations with exit codes](#monit_container_informations)
    4. [monit-docker with M/Monit](#monit_with_mmonit)
9. [Sub-command: stats](#sub-command_stats)
    1. [Basic commands](#stats_basic_commands)
    2. [Advanced commands](#stats_advanced_commands)
10. [Scheduling with cron](docs/cron.md)

## <a name="installation"></a>Installation

The examples below use Python 3 (CI tests Python 3.10 and 3.12). You need a Docker
daemon accessible to the account running monit-docker. For a local installation:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install monit-docker
monit-docker --help
```

Both modes are included in the same package. For an existing installation, use
`python -m pip install --upgrade monit-docker`; `serve` requires version 0.0.56
or newer. To run the published Docker image, see the
[simple](docs/simple.md#run-a-single-command-in-docker) or
[serve](docs/serve.md#run-serve-in-docker) example. Use a versioned image tag;
the release workflow does not update `latest`.

## <a name="quickstart"></a>Quickstart

Before running configured rules, use [`check-config`](docs/check-config.md) to validate
the YAML, imports, selectors and aliases without connecting to Docker.

### Simple mode: run a command and exit

Read the available statistics without taking any action:

```sh
monit-docker stats --output json
```

To check one container, replace `my-container` with an existing name:

```sh
monit-docker --name my-container monit --rsc status
echo $?  # 0 means running; 114 means no matching container
```

Continue with the [simple mode guide](docs/simple.md) for rule previews,
actions and scheduling. No background process is needed.

### Serve mode: keep monitoring and expose HTTP endpoints

For serve, Prometheus and Grafana together with the dashboard preloaded:

```sh
git clone https://github.com/decryptus/monit-docker.git
cd monit-docker/examples/monitoring
sh start.sh
```

Open `http://127.0.0.1:3000` as `admin` using the generated password in `.env`.
See the [Compose guide](docs/compose.md) for prerequisites, ports and persistent
history. This stack observes real containers and executes no remediation rules.
It also loads [three Prometheus alerts](docs/alerts.md) for an unreachable agent,
unhealthy collection and high container memory usage. View them in Prometheus;
enable optional [email and Slack notifications](docs/notifications.md) with
Alertmanager when you want messages as well.

To run only the agent after the Python installation above:

In one terminal, leave this command running:

```sh
monit-docker serve --interval 30
```

In a **second terminal**, check the first completed cycle:

```sh
curl -i http://127.0.0.1:9808/readyz
curl http://127.0.0.1:9808/metrics
```

`/readyz` returns 200 when monitoring has succeeded and the data is fresh; it
returns 503 until then. At least one container must match. Without action rules,
`serve` only observes containers. Stop it with Ctrl+C.

Continue with the [serve guide](docs/serve.md), then optionally connect
[Prometheus](docs/metrics.md#prometheus-configuration) and import the
[Grafana dashboard](docs/grafana.md). The latest measurements are kept in memory;
Prometheus provides history.

## Optional web interface

**monit-docker-ui** is a separate, lightweight Community interface: a compact
container overview on desktop and touch-friendly cards on mobile. It uses local
HTML, CSS and JavaScript with no frontend framework or external assets.

Nginx provides HTTPS and authentication. Read-only access is the default;
optional start, stop and restart controls require explicit agent configuration
and confirmation. The simple/cron mode remains independent of the UI.

Since **0.0.64**, label a container `monit-docker.protected=true` to block its
manual controls while keeping monitoring and automatic rules active. The live
demo shows the protected agent, UI and HTTPS proxy alongside two controllable
test containers. A public-demo banner explains that actions are shared and real;
stopped test containers are automatically started after about five minutes.

See the [UI installation and security guide](docs/ui.md) for Docker Compose,
credentials, certificates and manual-action settings. Available since **0.0.63**; the guide uses the published agent and UI images.

### Desktop

The screenshots below show the **live public demo (captured with 0.0.64)**, including its
shared-environment banner and protected infrastructure containers.

![Blue desktop interface showing container status, resources and optional controls](https://raw.githubusercontent.com/decryptus/monit-docker/master/ui/screenshots/demo-desktop.png)

<details>
<summary>View the mobile interface</summary>

<p align="center">
  <img src="https://raw.githubusercontent.com/decryptus/monit-docker/master/ui/screenshots/demo-mobile.png" alt="Mobile interface with responsive container cards and touch-friendly controls" width="320">
</p>

</details>

## <a name="environment_variables"></a>Environment variables

| Variable                   | Description                 | Default |
|:---------------------------|:----------------------------|:--------|
| `MONIT_DOCKER_CONFIG`      | Configuration file contents<br />(e.g. `export MONIT_DOCKER_CONFIG="$(cat monit-docker.yml)"`) |  |
| `MONIT_DOCKER_CONFFILE`    | Configuration file path     | /etc/monit-docker/monit-docker.yml |
| `MONIT_DOCKER_LOGFILE`     | Log file path               | /var/log/monit-docker/monit-docker.log |
| `MONIT_DOCKER_RUNTIMEDIR`  | Runtime directory path      | /run/monit-docker |

## <a name="sub-command_monit"></a>Sub-command: monit

### <a name="monit_basic_commands"></a>Basic commands

Restart containers with name starts with foo if memory usage percentage > 60% or cpu usage percentage > 90%:

`monit-docker --name 'foo*' monit --cmd-if 'mem_percent > 60 ? restart' --cmd-if 'cpu_percent > 90 ? restart'`

Stop containers with name starts with bar or foo and if cpu usage percentage greater than 60% and less than 70%:

`monit-docker --name 'bar*' --name 'foo*' monit --cmd-if '60 < cpu_percent < 70 ? stop'`

Kill containers with name starts with bar and status equal to paused or running:

`monit-docker --name 'bar*' monit --cmd-if 'status in (paused,running) ? kill'`

You can also use status argument, for example, restart containers with status paused or exited:

`monit-docker -s paused -s exited monit --cmd 'restart'`

Generate containers pidfile:

`monit-docker monit --rsc pid`

#### PHP-FPM graceful reload

The PHP-FPM examples below (including the Monit configuration) use
`kill -USR2 1` **inside the container**. They require the PHP-FPM master process
to be PID 1. According to the [PHP-FPM manual](https://github.com/php/php-src/blob/master/sapi/fpm/php-fpm.8.in),
`SIGUSR2` gracefully reloads the workers and reloads the FPM configuration and binary.

If PID 1 is a supervisor or a wrapper script, target the actual PHP-FPM master
PID inside the container instead. Obtain it from the PID file configured for
your PHP-FPM installation, or use your supervisor's documented reload command.
Do not target an arbitrary worker PID.

For a PHP-FPM master running as PID 1:

```sh
monit-docker --name foo_php_fpm monit --cmd '(kill -USR2 1)'
```

The Docker SDK `reload` action only refreshes container metadata; it does not
send `SIGUSR2` or reload PHP-FPM.

Reload php-fpm in container with image name contains /php-fpm/ if memory usage greater than 100 MiB:

`monit-docker --image '*/php-fpm/*' monit --cmd-if 'mem_usage > 100 MiB ? (kill -USR2 1)'`

Reload php-fpm in container with image name contains /php-fpm/ if /dev/shm percentage usage greater than 80%:

`monit-docker --image '*/php-fpm/*' monit --cmd '(bash -c "[ $(df /dev/shm | sed \"s/\%//;\$!d\" | awk \"{print \$5}\") -gt 80 ] && kill -USR2 1")'`

### <a name="monit_advanced_commands"></a>Advanced commands with configuration file or environment variable MONIT\_DOCKER\_CONFIG

#### Run commands with aliases declared in configuration file (e.g.: [monit-docker.yml.example](etc/monit-docker/monit-docker.yml.example)):

Restart container id 4c01db0b339c if condition alias @status\_not\_running is true:

`monit-docker --id 4c01db0b339c monit --cmd-if '@status_not_running ? restart'`

Execute commands alias @start\_pause containers with name starts with foo if condition alias @status\_not\_running is true:

`monit-docker --name 'foo*' monit --cmd-if '@status_not_running ? @start_pause'`

Remove force container group php if status is equal to running:

`monit-docker --ctn-group php monit --cmd-if 'status == running ? @remove_force'`

Restart containers group nodejs if memory usage percentage > 10% and cpu usage percentage > 60%:

`monit-docker --ctn-group nodejs monit --cmd-if '@mem_gt_10pct_and_cpu_gt_60pct ? restart'`

Remove force all containers:

`monit-docker monit --cmd '@remove_force'`

### <a name="monit_container_informations"></a>Container informations with exit codes

#### Container status

Run command below to get status with exit code for container named foo\_php\_fpm:

`monit-docker --name foo_php_fpm monit --rsc status`

An error occurred if exit code is greater than 100.

| Exit code | Description |
|:----------|:------------|
| 0         | Running     |
| 10        | Created     |
| 20        | Paused      |
| 30        | Restarting  |
| 40        | Removing    |
| 50        | Exited      |
| 60        | Dead        |
| 114       | Not found   |

#### Container CPU usage percentage

Run command below to get CPU usage percentage with exit code for container named foo\_php\_fpm:

`monit-docker --name foo_php_fpm monit --rsc cpu_percent`

An error occurred if exit code is greater than 100.

CPU percentages returned as exit codes are capped at 100 to avoid collisions with error codes and Unix exit-code overflow. The `stats` sub-command and conditional rules retain the raw CPU percentage, which may exceed 100 on multi-core hosts.

#### Container memory usage percentage

Run command below to get memory usage percentage with exit code for container named foo\_php\_fpm:

`monit-docker --name foo_php_fpm monit --rsc mem_percent`

An error occurred if exit code is greater than 100.

### <a name="monit_with_mmonit"></a>monit-docker with M/Monit

We can also monitoring containers cpu\_percent and mem\_percent resources with [M/Monit](https://mmonit.com).

#### Configuration examples

```
check program docker.foo_php_fpm.status with path "/usr/bin/monit-docker --name foo_php_fpm monit --rsc status"
    group monit-docker
    if status = 114 for 2 cycles then alert # container not found
    if status != 0 for 2 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd restart" # container not running

check program docker.foo_php_fpm.cpu with path "/usr/bin/monit-docker -s running --name foo_php_fpm monit --rsc cpu_percent"
    group monit-docker
    if status > 100 for 2 cycles then alert
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd '(kill -USR2 1)'"

check program docker.foo_php_fpm.mem with path "/usr/bin/monit-docker -s running --name foo_php_fpm monit --rsc mem_percent"
    group monit-docker
    if status > 100 for 2 cycles then alert
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd '(kill -USR2 1)'"

check process docker.foo_php_fpm.pid with pidfile /run/monit-docker/foo_php_fpm.pid
    group monit-docker
    if changed pid then alert

```

## <a name="sub-command_stats"></a>Sub-command: stats

### <a name="stats_basic_commands"></a>Basic commands

Get all resources statistics for all containers in json format:

`monit-docker stats --output json`

```json
{
  "flamboyant_chaplygin": {
    "status": "running",
    "mem_percent": 0.03,
    "net_tx": "0.0 B",
    "cpu_percent": 0,
    "mem_usage": "2.52 MiB",
    "io_read": "3.5 MB",
    "io_write": "0.0 B",
    "net_rx": "25.2 kB",
    "mem_limit": "7.27 GiB",
    "pid": "3943"
  }
}
{
  "practical_proskuriakova": {
    "status": "running",
    "mem_percent": 0.04,
    "net_tx": "0.0 B",
    "cpu_percent": 0,
    "mem_usage": "2.61 MiB",
    "io_read": "24.6 kB",
    "io_write": "0.0 B",
    "net_rx": "25.0 kB",
    "mem_limit": "7.27 GiB",
    "pid": "3990"
  }
}
```

Get all resources statistics for all containers in text format:

`monit-docker stats --output text`

```
flamboyant_chaplygin|mem_usage:2.52 MiB|mem_limit:7.27 GiB|mem_percent:0.03|cpu_percent:0.0|io_read:3.5 MB|io_write:0.0 B|net_tx:0.0 B|net_rx:43.5 kB|status:running
practical_proskuriakova|mem_usage:2.61 MiB|mem_limit:7.27 GiB|mem_percent:0.04|cpu_percent:0.0|io_read:24.6 kB|io_write:0.0 B|net_tx:0.0 B|net_rx:43.3 kB|status:running
```

### <a name="stats_advanced_commands"></a>Advanced commands with configuration file or environment variable MONIT\_DOCKER\_CONFIG

Get status and memory usage for group nodejs:

`monit-docker --ctn-group nodejs stats --rsc status --rsc mem_usage`

## Action failures and command behavior

If a command fails, `monit-docker` exits with code **116** and stops the current invocation; later commands and containers are not processed. Commands inside containers must complete successfully (exit code 0). Detached or streaming exec aliases do not supply a completion status and are not supported as successful monitored actions.

### Propagate a container command's exit code

With `monit --propagate-exit-code`, a failed synchronous command executed inside
a container returns its own exit code instead of 116:

```bash
monit-docker --name my-container monit --propagate-exit-code \
  --cmd '(sh -c "exit 42")'
echo $? # 42
```

The option is available with `monit --cmd` and `monit --cmd-if`, including command
aliases. It cannot be used with `stats` or resource-only checks.

| Outcome | Default | With `--propagate-exit-code` |
| --- | --- | --- |
| All executed commands succeed | 0 | 0 |
| A completed container command exits with code 1–255 | 116 | The command's code |
| An action fails without a valid completed exit code | 116 | 116 |
| Configuration, selection or Docker connection error | Existing error code | Existing error code |

Execution stops at the first failure. With several commands or containers, the
first failing command's code is returned; later actions are not attempted.
The existing execution order is preserved: rules using only PID/status run
before rules requiring measurements, for each container in Docker's listing
order. Use an exact `--name` or `--id` selector when checking one service.

If selected containers exist but no condition matches, the result is 0 because
no action failed. No matching container still returns 114. Detached/streaming
commands and invalid or unavailable exit codes remain errors (116); their
statuses are not propagated or wrapped. Docker lifecycle actions such as
`restart` retain their existing failure behavior.

For a Monit program check, the default nonzero code already supports a generic
`if status != 0` alert. Enable propagation when different command statuses need
different handling, for example a script using 2 for a critical result:

```monit
check program docker.my_container.health with path "/usr/bin/monit-docker --name my-container monit --propagate-exit-code --cmd '(/usr/local/bin/check-health)'"
    if status = 2 then alert
```

Propagated codes may overlap with monit-docker's own error codes. Consult the
logs to distinguish a command status from an agent error when the numbers match.
The flag is opt-in, so existing integrations keep returning 116 for failed actions.

An unknown `--ctn-group` is a configuration error (110), including when no groups are configured. Commands already evaluated before resource collection are not evaluated again after collection.

`reload` refreshes the Docker SDK object's metadata only; it does not reload application workers or configuration. For PHP-FPM, use `(kill -USR2 1)` only when its master is PID 1 inside the container, as explained in [PHP-FPM graceful reload](#php-fpm-graceful-reload). Otherwise, send `SIGUSR2` to the actual PHP-FPM master PID.

Commands inside parentheses use Docker exec, without an implicit shell. For redirections, pipes or shell expansion, explicitly use a shell, for example `(sh -c "echo foo > /tmp/bar")`.

## Development

The codebase is being separated into a transport-neutral monitoring core and
thin delivery interfaces. See [Architecture](docs/architecture.md) for the
dependency rules, compatibility guarantees, and Community/control-plane
boundary.

Install the dependencies and run the regression tests with Python 3:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Build the documentation and check for broken internal references:

```sh
python -m pip install -r docs/requirements.txt
python -m sphinx -n -W --keep-going -b html docs docs/_build/html
```

Build the checked-out source with `docker build -t monit-docker:local .`. The Dockerfile installs this checkout in a virtual environment instead of fetching the published `monit-docker` package.

## Lightweight cron mode

Run one cycle with a process lock and persistent cooldowns, without a server:

```sh
monit-docker --name 'web*' cron --state-file /var/lib/monit-docker/web.json \
  --cooldown 300 --dry-run --cmd-if 'mem_percent > 90 ? restart'
```

Review the JSON decisions, then remove `--dry-run` to execute eligible actions.
To wait for a sustained condition before acting, add `--trigger-after` and
`--max-gap`; see the [trigger delay guide](docs/trigger-delay.md) for cron and serve.

The default cooldown is five minutes per rule and container. A busy job exits
with 117; invalid or unwritable state exits with 118. Existing `monit` and `stats`
commands retain their behavior. `monit --dry-run --cmd ...` also previews actions.
See [cron setup, scheduling, state and failure semantics](docs/cron.md).

Automatic `restart` actions in `cron` and `serve` default to three attempts per
container ID, persisted in the state file and shared across rules. Configure
`--max-restarts` and explicitly rearm with `restart-reset` after intervention;
see [restart limits](docs/restart-limit.md). Monitoring continues when blocked.

## Continuous monitoring, Prometheus and Grafana

```sh
monit-docker --name 'web*' serve --interval 30
```

The read-only HTTP listener defaults to `127.0.0.1:9808`: `/healthz`, `/readyz`,
`/v1/status` and `/metrics`. Requests read the latest completed cycle from memory.
Metrics are not persisted locally; Prometheus stores history. Optional remediation
rules reuse cron locking and persistent cooldowns.

See [serve usage and API](docs/serve.md), the [complete metrics reference](docs/metrics.md),
and [Grafana setup](docs/grafana.md). An [importable Grafana dashboard](examples/grafana/monit-docker.json)
includes agent health, CPU, memory, network, block I/O and action decisions.

![Grafana overview with synthetic demonstration data](https://raw.githubusercontent.com/decryptus/monit-docker/master/docs/images/grafana-overview.png)

*Real Grafana rendering with synthetic demonstration data. See the
[gallery and setup guide](docs/grafana.md) for details and larger panel views.*

## Docker Hub and PyPI releases

Merging a new stable version into `master` builds and tests the Docker image and
Python distributions, creates the `vX.Y.Z` tag, then publishes
`decryptus/monit-docker:X.Y.Z`, `decryptus/monit-docker:vX.Y.Z` and the Python
package on PyPI. Manual stable tag pushes are also supported.
Pull requests validate without publishing; Docker Hub's `latest` is not updated.
See the [Docker Hub setup](docs/dockerhub.md) for `DOCKERHUB_TOKEN` and the
[PyPI setup](docs/pypi.md) for password-free Trusted Publishing.
