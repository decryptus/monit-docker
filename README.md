# monit-docker project

[![PyPI pyversions](https://img.shields.io/pypi/pyversions/monit-docker.svg)](https://pypi.org/project/monit-docker/)
[![PyPI version shields.io](https://img.shields.io/pypi/v/monit-docker.svg)](https://pypi.org/project/monit-docker/)
[![Docker Cloud Build Status](https://img.shields.io/docker/cloud/build/decryptus/monit-docker)](https://hub.docker.com/r/decryptus/monit-docker)
[![Documentation Status](https://readthedocs.org/projects/monit-docker/badge/?version=latest)](https://monit-docker.readthedocs.io/)

monit-docker is a free and open-source, we develop it to monitor container status or resources
and execute some commands inside containers or manage containers with dockerd, for example:
 - reload php-fpm if memory usage is too high
 - restart container if status is not running
 - remove all containers

## Table of contents
1. [Quickstart](#quickstart)
2. [Installation](#installation)
3. [Environment variables](#environment_variables)
4. [Sub-command: monit](#sub-command_monit)
    1. [Basic commands](#monit_basic_commands)
    2. [Advanced commands](#monit_advanced_commands)
    3. [Container informations with exit codes](#monit_container_informations)
    4. [monit-docker with M/Monit](#monit_with_mmonit)
5. [Sub-command: stats](#sub-command_stats)
    1. [Basic commands](#stats_basic_commands)
    2. [Advanced commands](#stats_advanced_commands)

## <a name="quickstart"></a>Quickstart

Using monit-docker in Docker with crond

`docker-compose up -d`

See [docker-compose.yml](docker-compose.yml) and MONIT\_DOCKER\_CRONS environment variable to configure commands.

## <a name="installation"></a>Installation

`pip install monit-docker`

## <a name="environment_variables"></a>Environment variables

| Variable                | Description                 | Default |
|:------------------------|:----------------------------|:--------|
| `MONIT_DOCKER_CONFIG`   | Configuration file contents<br />(e.g. `export MONIT_DOCKER_CONFIG="$(cat monit-docker.yml)"`) |  |
| `MONIT_DOCKER_CONFFILE` | Configuration file path     | /etc/monit-docker/monit-docker.yml |
| `MONIT_DOCKER_LOGFILE`  | Log file path               | /var/log/monit-docker/monit-docker.log |

## <a name="sub-command_monit"></a>Sub-command: monit

### <a name="monit_basic_commands"></a>Basic commands

Restart containers with name starts with foo if memory usage percentage > 60% or cpu usage percentage > 90%:

`monit-docker monit --name 'foo*' --cmd-if 'mem_percent > 60 ? restart' --cmd-if 'cpu_percent > 90 ? restart'`

Stop containers with name starts with bar or foo and if cpu usage percentage greater than 60% and less than 70%:

`monit-docker monit --name 'bar*' --name 'foo*' --cmd-if '60 > cpu_percent < 70 ? stop'`

Kill containers with name starts with bar and status equal to pause or running:

`monit-docker monit --name 'bar*' --cmd-if 'status in (pause,running) ? kill'`

You can also use status argument, for example, restart containers with status paused or exited:

`monit-docker -s paused -s exited monit --cmd 'restart'`

Run command in container with image name contains /php-fpm/ and if memory usage > 100 MiB:

`monit-docker --image '*/php-fpm/*' monit --cmd-if 'mem_usage > 100 MiB ? (kill -USR2 1)'`

### <a name="monit_advanced_commands"></a>Advanced commands with configuration file or environment variable MONIT\_DOCKER\_CONFIG

##### Run commands with aliases declared in configuration file (e.g.: [monit-docker.yml.example](etc/monit-docker/monit-docker.yml.example)):

Restart container id 4c01db0b339c if condition alias @status\_not\_running is true:

`monit-docker monit --id 4c01db0b339c --cmd-if '@status_not_running ? restart'`

Execute commands alias @start\_pause containers with name starts with foo if condition alias @status\_not\_running is true:

`monit-docker monit --name 'foo*' --cmd-if '@status_not_running ? @start_pause'`

Remove force container group php if status is equal to running:

`monit-docker --ctn-group php monit --cmd-if 'status == running ? @remove_force'`

Restart containers group nodejs if memory usage percentage > 10% and cpu usage percentage > 60%:

`monit-docker --ctn-group nodejs monit --cmd-if '@mem_gt_10pct_and_cpu_gt_60pct ? restart'`

Remove force all containers:

`monit-docker monit --cmd '@remove_force'`

### <a name="monit_container_informations"></a>Container informations with exit codes

##### Container status

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

##### Container CPU usage percentage

Run command below to get CPU usage percentage with exit code for container named foo\_php\_fpm:

`monit-docker --name foo_php_fpm monit --rsc cpu_percent`

An error occurred if exit code is greater than 100.

##### Container memory usage percentage

Run command below to get memory usage percentage with exit code for container named foo\_php\_fpm:

`monit-docker --name foo_php_fpm monit --rsc mem_percent`

An error occurred if exit code is greater than 100.

### <a name="monit_with_mmonit"></a>monit-docker with M/Monit

We can also monitoring containers cpu\_percent and mem\_percent resources with [M/Monit](https://mmonit.com).

##### Configuration examples

```
check program docker.foo_php_fpm.status with path "/usr/bin/monit-docker --name foo_php_fpm monit --rsc status"
    group monit-docker
    if status = 114 for 2 cycles then alert # container not found
    if status != 0 for 2 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd restart" # container not running

check program docker.foo_php_fpm.cpu with path "/usr/bin/monit-docker -s running --name foo_php_fpm monit --rsc cpu_percent"
    group monit-docker
    if status > 100 for 2 cycles then alert
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd reload"

check program docker.foo_php_fpm.mem with path "/usr/bin/monit-docker -s running --name foo_php_fpm monit --rsc mem_percent"
    group monit-docker
    if status > 100 for 2 cycles then alert
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd '(kill -USR2 1)'"
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
    "mem_limit": "7.27 GiB"
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
    "mem_limit": "7.27 GiB"
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

`monit-docker --ctn-group nodejs --rsc status --rsc mem_usage`
