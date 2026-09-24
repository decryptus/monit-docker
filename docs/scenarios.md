# Named scenarios (since 0.0.70)

Define a monitoring job once, then run it by name. A scenario combines container
selection, rules or resources, and execution settings. It uses the existing
engine, state lock, maintenance, cooldown, restart budget and journal.

## Define once, run by name

Add this section to `/etc/monit-docker/monit-docker.yml`:

```yaml
scenarios:
  web-guard:
    description: Restart web containers after sustained high memory usage
    mode: cron
    select:
      name: ['web-*']
    rules:
      - 'mem_percent > 90 ? restart'
    state-file: /var/lib/monit-docker/web-guard.json
    cooldown: 300
    max-restarts: 3
    trigger-after: 120
    max-gap: 90

  web-stats:
    description: Read web container CPU and memory usage
    mode: stats
    select:
      name: ['web-*']
    resources: [cpu_percent, mem_usage]
    output: json
```

```bash
monit-docker scenario list
monit-docker scenario show web-guard
monit-docker check-config
monit-docker run web-stats
monit-docker run web-guard --dry-run
monit-docker run web-guard
```

`scenario list` prints names, modes and descriptions as JSON. `scenario show`
prints the rendered definition as JSON; its output can include command arguments.
Both validate offline without Docker, state files, audit writes or HTTP listeners.
`check-config` also validates every scenario, including dormant rules/references.

Use `-c FILE` or the existing environment variable for another configuration:

```bash
export MONIT_DOCKER_CONFFILE=/etc/monit-docker/production.yml
monit-docker run web-guard --dry-run
```

`cron` (the default scenario mode) performs one cycle. Schedule the short command
with the operating system's cron, for example every minute:

```text
* * * * * /usr/local/bin/monit-docker run web-guard
```

Adapt the executable path. Use a writable persistent state directory and a
consistent account. This example requires two minutes of sustained observations,
with no gap exceeding 90 seconds. A first match does not immediately restart.
Dry runs do not build durable observation streaks or consume cooldown/restart
reservations; they may create a state lock file and journal simulated decisions.

## Modes and settings

| Mode | Behavior | Mode-specific fields |
| --- | --- | --- |
| `stats` | Read measurements once | `resources`, `output` |
| `cron` | Evaluate rules once with persistent policies | `rules`, `state-file`, `cooldown`, `max-restarts`, `trigger-after`, `max-gap`, `dry-run` |
| `serve` | Run continuously using HTTPdis | All `cron` fields, plus `resources`, `bind`, `port`, `interval`, `stale-after` |

All modes accept `description`, `select` (or `all: true`), `client`,
`client-from-env`, `event-window`, `audit-file`, `audit-max-bytes` and `audit-files`.
Omitted settings use the existing CLI defaults. `cron` requires rules and a state
file; `serve` requires a state file with rules. Existing numeric bounds apply.
`run --dry-run` requires rules and can only enable simulation; it never disables
a configured `dry-run: true`.

For continuous execution, use `mode: serve`, `interval: 30` and, when configured,
a `max-gap` greater than the interval. Each `run` starts one scenario. This first
version does not combine scenarios, define ordered workflow steps, enable manual
HTTP actions, or launch a scheduler. The full existing CLI remains available.

## Selection and reuse

`select` accepts `name`, `id`, `image`, `label`, `status` and `group`. Each takes
one string or a nonempty list of strings. `group` refers to an existing
`ctn-groups` entry. Globs/regular expressions follow the existing selector syntax.
As in the full CLI, name/id/image/label matches are alternatives (OR); status
applies in addition. Group selection may be combined with status only.

An empty/missing `select` is rejected unless `all: true` is present. The two are
mutually exclusive. Global CLI selectors and Docker client options are rejected
with `run` and `scenario`: keep targets in the definition. Global configuration,
logging, audit and event-window options remain available; scenario audit and
event-window settings take precedence when specified.

Names match `[a-z0-9][a-z0-9-]{0,63}` and are selected by exact name. Existing
condition/action aliases and directory resources work in scenario rules/resources.
Permission probes still require Python 3 in the container and an explicit access
identity in the directory group.

Scenarios support existing Mako variables and section imports:

```yaml
scenarios:
  '@import_scenario': scenarios.yml
```

The imported file contains scenario names without a second `scenarios:` wrapper.
Imports are relative to the main configuration directory. Existing template
evaluation order applies: imported file templates use global/section variables;
main-file entries may also use entry-local `vars`.

## State and audit

Use one state file per Docker host and job. Keep the same path when converting
an existing command: cooldowns, restart budgets and maintenance continue to apply.
Relative state/audit paths remain relative to the working directory; absolute
paths avoid surprises with cron or systemd. Renaming a scenario does not reset
state; changing its state file starts a separate policy history.

Maintenance and `restart-reset` use that same state file and the exact container
ID. `run` preserves the existing mode's audit identity: `cron` and `serve` actions
are automatic even when launched from a terminal. Generated messages remain
English. Action errors keep existing exit codes; invalid scenarios return 110
before connecting to Docker.

The configuration is rendered once, validated, then handed to the runner in
memory. Execution does not reload a potentially different configuration.
