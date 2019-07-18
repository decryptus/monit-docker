# monit-docker project

## Installation

pip install monit-docker

## Commands:

Restart containers with name starts with foo if memory percent > 60% or cpu percent > 90%:

`monit-docker monit --name "foo*" --cmd-if 'mem_percent > 60 ? restart' --cmd-if 'cpu_percent > 90 ? restart'`

Restart containers with name starts with bar and foo if cpu percent greather than 60% and less than 70%:

`monit-docker monit --name "bar*" --name "foo*" --cmd-if '60 > cpu_percent < 70 ? restart'`

Restart containers with name starts with bar and status equal to pause or running:

`monit-docker monit --name "bar*" --cmd-if 'status in (pause,running) ? restart'`

Run command in container with image name contains /php-fpm/ and if memory usage > 100 MiB:

`monit-docker --image '*/php-fpm/*' monit --cmd-if 'mem_usage > 100 MiB ? (kill -USR2 1)'`

#### With aliases

Run commands with aliases declared in configuration file:

`monit-docker monit --id 4c01db0b339c --cmd-if '@status_not_running ? restart'`

`monit-docker monit --name "foo*" --cmd-if '@status_not_running ? @start_pause'`
