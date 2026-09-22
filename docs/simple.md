# Simple mode: one command, then exit

Choose this mode for a manual check, a script, Monit/M/Monit integration, or a
scheduled job. It starts no HTTP server and requires neither Prometheus nor
Grafana. You can keep using it even if you never enable `serve`.

Install monit-docker using the [installation instructions](https://github.com/decryptus/monit-docker#installation).
The account running it needs access to the Docker daemon. The examples use the
local daemon; replace `my-container` with an existing container name.

## 1. Read statistics

```sh
monit-docker stats --output json
monit-docker --name my-container stats --rsc mem_usage --rsc cpu_percent
```

`stats` prints measurements and exits. It does not execute remediation actions.
Use `--output text` for text output. Stopped containers may have a status without
CPU or memory measurements.

## 2. Check a status in a script

```sh
monit-docker --name my-container monit --rsc status
echo $?
```

`monit --rsc status` communicates the container status through the process exit
code: 0 means running, 50 means exited, and 114 means no container matched.
See the [complete status codes and Monit examples](https://github.com/decryptus/monit-docker#monit_container_informations).
Use one exact name or ID when a script expects the status of one container.

Global options such as `--name`, `--id` and `--client-from-env` go **before** the
subcommand; options such as `--rsc`, `--cmd-if` and `--dry-run` go after it.
Quote wildcard selectors, for example `--name 'web*'`.

## 3. Preview a rule, then enable its action

Preview restarting a container whose memory usage exceeds 90%:

```sh
monit-docker --name my-container monit --dry-run \
  --cmd-if 'mem_percent > 90 ? restart'
```

This reads real measurements and prints decisions for matching actions, without
executing them. No decision is printed when the condition does not match.
After checking the container selection and condition, remove `--dry-run` to
allow the restart. A successful `monit` invocation exits with 0, including when
no rule matched; action failures normally return 116.

`monit` performs one cycle each time it is invoked. It does not remember earlier
actions or impose a cooldown. Use `cron` below when repeated invocations need
that coordination. See the [command reference](https://github.com/decryptus/monit-docker#sub-command_monit)
for aliases, multiple rules and commands executed inside containers.

## 4. Optionally repeat with cron

`cron` is still a one-shot command, not a daemon. Compared with `monit`, it adds
a process lock and a persistent delay between attempts of the same rule on the
same container:

```sh
monit-docker --name my-container cron \
  --state-file "$HOME/.local/state/monit-docker/my-container.json" \
  --cooldown 300 --dry-run --cmd-if 'mem_percent > 90 ? restart'
```

Use a writable, persistent state directory. After reviewing the preview, remove
`--dry-run` and schedule the command with the operating system's cron. The
`cron` subcommand does not schedule itself. See [cron scheduling and state](cron.md)
for a crontab example, executable paths, locking and failure behavior.

## Run a single command in Docker

For a local Docker Engine using `/var/run/docker.sock`:

```sh
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  decryptus/monit-docker:0.0.56 monit-docker stats --output json
```

The command after the image selects a one-shot invocation instead of the image's
default cron runner. Docker socket access allows control of that daemon. For
scheduled actions with cooldowns, also mount the entire state directory so the
JSON state and adjacent lock file are shared across invocations.

## When to use serve instead

Use [serve mode](serve.md) when you want a long-running monitoring process,
HTTP status endpoints, or metrics that Prometheus can scrape. You do not need
to switch modes to run simple checks or scheduled rules. Both modes use the
same selectors and rule syntax.

If a command fails, start with [troubleshooting](troubleshooting.md).
