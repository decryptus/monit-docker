# monit-docker project

monit-docker is a free and open-source, we develop it to monitor container status or resources
and execute some commands inside containers or manage containers with dockerd, for example:
 - reload php-fpm if memory usage is too high
 - restart container if status is not running
 - remove all containers

## Quickstart

Using monit-docker in Docker with crond

`docker-compose up -d`

See [docker-compose.yml](docker-compose.yml) and MONIT\_DOCKER\_CRONS environment variable to configure commands.

## Commands:

#### Simple commands

Restart containers with name starts with foo if memory percent > 60% or cpu percent > 90%:

`monit-docker monit --name 'foo*' --cmd-if 'mem_percent > 60 ? restart' --cmd-if 'cpu_percent > 90 ? restart'`

Stop containers with name starts with bar or foo and if cpu percent greather than 60% and less than 70%:

`monit-docker monit --name 'bar*' --name 'foo*' --cmd-if '60 > cpu_percent < 70 ? stop'`

Kill containers with name starts with bar and status equal to pause or running:

`monit-docker monit --name 'bar*' --cmd-if 'status in (pause,running) ? kill'`

Run command in container with image name contains /php-fpm/ and if memory usage > 100 MiB:

`monit-docker --image '*/php-fpm/*' monit --cmd-if 'mem_usage > 100 MiB ? (kill -USR2 1)'`

#### Advanced commands with configuration file or environment variable MONIT\_DOCKER\_CONFIG

##### Run commands with aliases declared in configuration file (e.g.: [monit-docker.yml.example](etc/monit-docker/monit-docker.yml.example)):

Restart container id 4c01db0b339c if condition alias @status\_not\_running is true:

`monit-docker monit --id 4c01db0b339c --cmd-if '@status_not_running ? restart'`

Execute commands alias @start\_pause containers with name starts with foo if condition alias @status\_not\_running is true:

`monit-docker monit --name 'foo*' --cmd-if '@status_not_running ? @start_pause'`

Remove force container group php if status is equal to running:

`monit-docker monit --ctn-group php --cmd-if 'status == running ? @remove_force'`

Restart containers group nodejs if memory percent > 10% and cpu percent > 60%:

`monit-docker monit --ctn-group nodejs --cmd-if '@mem_gt_10pct_and_cpu_gt_60pct ? restart'`

Remove force all containers:

`monit-docker monit --cmd '@remove_force'`

## Environment variables

| Variable                | Description                 | Default |
|:------------------------|:----------------------------|:--------|
| `MONIT_DOCKER_CONFIG`   | Configuration file contents<br />(e.g. `export MONIT_DOCKER_CONFIG="$(cat monit-docker.yml)"`) |  |
| `MONIT_DOCKER_CONFFILE` | Configuration file path     | /etc/monit-docker/monit-docker.yml |
| `MONIT_DOCKER_LOGFILE`  | Log file path               | /var/log/monit-docker/monit-docker.log |

## monit-docker with M/Monit

We can also monitoring containers cpu\_percent and mem\_percent resources with [M/Monit](https://mmonit.com).

#### Configuration examples

```
check program docker.foo_php_fpm.cpu with path "/usr/bin/monit-docker --name foo_php_fpm monit --rsc cpu_percent"
    group monit-docker
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd reload"

check program docker.foo_php_fpm.mem with path "/usr/bin/monit-docker --name foo_php_fpm monit --rsc mem_percent"
    group monit-docker
    if status > 70 for 2 cycles then alert
    if status > 80 for 4 cycles then exec "/usr/bin/monit-docker --name foo_php_fpm monit --cmd '(kill -USR2 1)'"
```
