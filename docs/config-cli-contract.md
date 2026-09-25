# Configuration and CLI compatibility contract

**Status: 0.0.x baseline, reviewed for the proposed 0.0.77 release.** This reference
records the current public behavior and the decisions still needed before 1.0.
It does not declare the HTTP API, metrics or journal schema stable. Follow the
[roadmap](roadmap.md) for the complete release criteria.

Use full option names and explicit selectors in automation. Global options go
before the subcommand; that subcommand's options go after it:

```sh
monit-docker -c config.yml --name 'web-*' stats --rsc status
monit-docker -c config.yml check-config --output json
monit-docker -c config.yml run web-guard --dry-run
```

## Configuration source and precedence

1. `-c FILE` selects a file; otherwise use `MONIT_DOCKER_CONFFILE`, then
   `/etc/monit-docker/monit-docker.yml`.
2. If the selected path exists, load it. A read or parse error does **not** fall
   back to the environment.
3. If it does not exist, use nonempty `MONIT_DOCKER_CONFIG` as inline YAML.
4. Without either source, historical `stats`, `monit`, `cron` and `serve` use an
   empty configuration. `check-config`, `run` and `scenario` instead return 110.

The root must be a YAML mapping. `{}` is valid; an empty document, `null` or a
list is not. The checked loader requires string keys and mapping-valued sections.
It rejects unknown sections and entry fields. The historical loader is more
permissive and can ignore unused fields; `stats` does not load command/condition
aliases. Validate with `check-config` before deployment.

| Section | Entry structure | Meaning |
| --- | --- | --- |
| `general` | Mapping | Template context, not a mapping of CLI defaults |
| `vars` | Mapping | Shared template variables |
| `clients` | `NAME: {config: MAPPING}` | Docker SDK connection options; `tls` may be a boolean or mapping |
| `ctn-groups` | `NAME: {match: [STRING, ...]}` | Nonempty list of `name:`, `id:`, `image:` or `label:` patterns |
| `dir-groups` | `NAME: {paths: [STRING, ...], access: MAPPING}` | Nonempty absolute container paths; `access` is optional unless an access probe is requested |
| `conditions` | `NAME: {expr: [STRING, ...]}` | Nonempty list of conditions combined with AND |
| `commands` | `NAME: {exec: [ACTION, ...]}` | Nonempty ordered action sequence |
| `scenarios` | `NAME: MAPPING` | A named `stats`, `cron` or `serve` job |

Command/condition aliases and directory-group names use an ASCII letter followed
by at most 64 letters, digits, underscores, dots or hyphens. Scenario names match
`[a-z0-9][a-z0-9-]{0,63}` and are selected exactly. Client and container-group names
have no equivalent restrictive naming pattern in the checked loader, but must
be nonempty strings. Prefer simple printable names.

Entries may have local `vars` and `@import_vars`. Section imports use
`@import_client`, `@import_ctn-group`, `@import_dir-group`, `@import_condition`,
`@import_command` or `@import_scenario`; accept one path or a nonempty list. Imported
files contain entries directly, without repeating the section wrapper. Relative
imports resolve against the main file's directory (the working directory for
inline configuration). Later imports override earlier entries; main-file entries
override imported entries **as a whole**, without deep merging. Imports are not
a recursive include mechanism.

Mako templates run during rendering and must be trusted. They can access `ENV`,
the `my` YAML helper and the supplied context. An imported file is rendered before
its entry-local variables are available. Offline validation does not sandbox
template execution. State/audit paths resolve against the working directory,
not the configuration directory; absolute paths are preferable for scheduled jobs.

Directory access identities contain exactly `uid`, `gid` and `groups`: integer
IDs from 0 through 4294967294, and 1..128 distinct supplementary group IDs including
`gid`. Booleans are not IDs. Paths cannot contain ASCII control characters;
duplicates are removed in order. Access probes require Python 3 in the target
Linux container; see [access checks](access-checks.md) for runtime restrictions.

## Docker client selection

`--client-from-env` takes precedence over `--client` and configured clients.
Otherwise, `--client NAME` selects an entry, or the first loaded client is used.
With no configured clients the historical runtime uses Docker environment
settings, defaulting `DOCKER_HOST` to `unix:///var/run/docker.sock` when absent.
An explicit but unknown client is rejected by checked configuration. See the
legacy discrepancy under [open decisions](#open-decisions-before-10).

## Container selectors

| Input | Current behavior |
| --- | --- |
| `--name`, `--id`, `--image`, `--label` | Repeatable; each value is also split at commas, with surrounding whitespace removed |
| Several patterns or kinds | OR: any name, ID, image tag or label value can select the container |
| `-s STATUS` | Repeatable status alternatives, applied in addition to the pattern selection |
| `--ctn-group NAME` | Repeatable exact group names; their patterns form a union and replace direct name/ID/image/label patterns |
| No patterns/groups | All containers allowed by the status filter, including stopped containers |
| Ordinary pattern | Case-sensitive shell-style glob, matching the whole value |
| `~EXPRESSION` | Case-sensitive Python regular expression matched from the start; add `$` for an end anchor or `.*` for a leading search |
| 12-character alphanumeric ID pattern | Automatically gets a trailing `*` for legacy short-ID matching |
| Label pattern | Matches label **values**, not keys or Docker's `key=value` filter syntax |
| Image pattern | Matches image tags, not image IDs/digests |

Quote patterns to prevent shell expansion. Direct selectors, including scenario
selectors, split commas even inside regular expressions. For a quantifier such
as `{1,3}`, use a container group: group `match` expressions do not split commas.
For example:

```yaml
ctn-groups:
  workers:
    match: ['name:~worker-[0-9]{1,3}$']
```

Use `--ctn-group workers`, or scenario `select: {group: workers}`. Group matches
do not support status expressions; use `-s` or scenario `select.status`.
An empty result is error 114, not a successful no-op. Container iteration order
comes from Docker and is not promised to be sorted.

## Global options

| Options | Default / constraints |
| --- | --- |
| `-c` | Configuration precedence above |
| `--client`, `--client-from-env` | Client precedence above |
| `--name`, `--id`, `--image`, `--label`, `--ctn-group`, `-s` | Selection above; status is one of `running`, `created`, `paused`, `restarting`, `removing`, `exited`, `dead` |
| `-l` | `info`; `critical`, `error`, `warning`, `info`, `debug` |
| `--logfile` | `MONIT_DOCKER_LOGFILE` or `/var/log/monit-docker/monit-docker.log` |
| `--runtimedir` | `MONIT_DOCKER_RUNTIMEDIR` or `/run/monit-docker` |
| `--event-window` | 300 seconds; integer 1..86400 |
| `--audit-file` | `MONIT_DOCKER_AUDIT_FILE`; otherwise `audit/events.jsonl` beside the state file, or `$XDG_STATE_HOME/monit-docker/audit/events.jsonl` (`~/.local/state` fallback) when a journal is needed |
| `--audit-max-bytes` | 5242880; integer at least 65536 |
| `--audit-files` | 5; integer 1..100 |

An empty audit path is invalid. Export/forwarding and authenticated journal reads
require an explicit audit path (the environment variable also counts).
Environment-derived configuration/log/runtime defaults are read when the CLI
module is imported; set them before launching the process.

## Rules and action aliases

`--cmd` and `--cmd-if` are aliases for the same repeatable option. A rule is an
unconditional action, `CONDITION ? ACTION`, or `@condition_alias ? @command_alias`;
inline and aliased sides can be mixed. A command alias expands to its ordered
action list; a condition alias expands to conditions combined with AND. Alias
entries do not recursively reference other aliases.

Docker actions are `start`, `stop`, `remove`, `reload`, `restart`, `kill`, `pause`,
`unpause`. Parenthesized text such as `(sh -c "echo ready")` is a Docker exec
command; use an explicit shell when shell syntax is needed. In a command alias,
an action can be a string or a single-key mapping whose options are `args` (list)
and `kwargs` (mapping), passed to the Docker SDK. Offline validation does not
guarantee that every SDK keyword is supported. Completed exec status is required;
detached or streaming execs cannot be reported as successful monitored actions.

Numeric comparisons use `==`, `!=`, `<`, `<=`, `>`, `>=`, nonnegative numeric
literals and, where supported, byte units such as `MiB`. State membership uses
`in (paused,running)` or `not in (paused,running)`, without spaces inside the list.
Resource-specific bounds/operators are described in the check guides. Conditions
for one directory group must be satisfied together by at least one path in that
group. Separate groups and ordinary conditions are combined with AND.

Rules requiring only status/PID/health run before metric-dependent rules, even
when their CLI order is interleaved. Within each phase, input order is preserved.
Metric rules only run when the container is running or paused. This two-phase
behavior and the historical chained comparisons need explicit consideration
when converting long commands into scenarios.

## Subcommands and outputs

| Command | Options / behavior |
| --- | --- |
| `stats` | Repeatable `--rsc`; `--output json` (default) or `text`; no remediation |
| `monit` | Repeatable `--rsc` **or** repeatable `--cmd`/`--cmd-if`; `--dry-run` and `--propagate-exit-code` require rules |
| `cron` | Repeatable `--cmd`/`--cmd-if` and `--state-file` required; policy options below; `--dry-run`, `--propagate-exit-code` |
| `serve` | Continuous collection; repeatable `--rsc` and optional `--cmd`/`--cmd-if`; state required with rules; policy/listener options below |
| `check-config` | `--output text` (default) or `json`; optional repeatable `--cmd`/`--cmd-if` checked without running them |
| `scenario list` | JSON array of `{name, mode, description}`, sorted by name; no positional scenario name |
| `scenario show NAME` | One rendered scenario object as JSON; can contain command arguments; not a fully expanded default-option dump |
| `run NAME` | Run one exact scenario; optional `--dry-run` can enable but never disable simulation |
| `maintenance` | `--state-file`, exact lowercase 64-hex `--container-id`, `--duration` required; 0 resumes, 1..86400 seconds pauses automatic actions; JSON `{container_id, maintenance_until}` |
| `restart-reset` | `--state-file` and exact lowercase 64-hex `--container-id` required; JSON `{container_id, status: "rearmed"}`; requires recorded attempts |
| `audit-export` | `--format jsonl` (default) or `csv`; optional `--category action\|notification` and `--since` with timezone |
| `audit-send` | Export filters plus required `--url` HTTPS receiver and optional `--token-file`; forwards JSON events and reports acknowledgements on stderr |

`stats --output json` emits one JSON object per container, not a single array.
Each maps the container name to requested resource values. Memory, disk and I/O
values are human-readable unit strings where applicable. Text uses
`NAME|RESOURCE:VALUE|...`. `monit` text keeps raw units. Unavailable displayed
values may be null. Do not parse diagnostic logs as command output.

With no `--rsc`, `stats` and `serve` request `mem_usage`, `mem_limit`, `mem_percent`,
`cpu_percent`, `io_read`, `io_write`, `net_tx`, `net_rx`, `status`, `pid`, `health`.
Additional resources are `oom_events`, `starts_recent`, `pids_current`,
`pids_limit`, `pids_percent`, and filesystem resources such as `disk_percent[data]`.
See [filesystem](filesystems.md), [access](access-checks.md) and
[runtime](runtime-checks.md) references for all resource meanings and prerequisites.

Rules emit JSON action decisions containing `container_id`, `rule`, `command`
and `status`. Current statuses include `executed`, `dry-run`, `cooldown`,
`pending`, `restart-limit`, `maintenance`. Nonmatching rules emit no decision.
Docker exec output is not passed through as a structured stdout result.
The first action failure aborts the remaining actions and containers.

`check-config --output json` returns `{valid, errors, summary}` on stdout.
Errors contain `location` and `message`; validation stops at the first error.
Summary counts `clients`, `groups`, `commands`, `conditions`, `rules`, plus
`scenarios` when present. It validates dormant aliases/groups/scenarios as well
as supplied rules. `scenario list` validates all scenarios; `show` and `run`
validate the selected scenario after checking configuration shapes. Their
configuration errors go to stderr and return 110.

These inspection commands do not connect to Docker or create agent state/log
files. `maintenance` and `restart-reset` also avoid Docker but mutate local state
and write audit events. A dry run collects live Docker data; cron/serve can create
a lock and audit simulated decisions while preserving durable policy state.

## Policy and listener options

| Option | Modes | Default / constraints |
| --- | --- | --- |
| `--state-file` | `cron`, `serve` | Nonempty path; required in cron, or serve with rules/manual actions |
| `--cooldown` | `cron`, `serve` | 300 seconds; finite and nonnegative |
| `--max-restarts` | `cron`, `serve` | 3; positive integer, attempts per container until rearm |
| `--trigger-after` | `cron`, `serve` | 0; finite and nonnegative; positive value requires rules and `--max-gap` |
| `--max-gap` | `cron`, `serve` | Finite positive seconds; only with positive trigger delay; must exceed interval in serve |
| `--bind`, `--port` | `serve` | IPv4 `127.0.0.1`, integer 9808 (1..65535) |
| `--interval` | `serve` | 30 seconds; finite and at least 0.1 |
| `--stale-after` | `serve` | `max(90, 3 * interval)`; finite and at least interval |
| `--dry-run` | `monit`, `cron`, `serve`, `run` | Off; requires rules; incompatible with manual HTTP actions |
| `--allow-actions` | `serve` | Off; requires state, action token and exact HTTPS origin |
| `--action-origin`, `--action-token-file` | `serve` | Require `--allow-actions`; origin `https://host[:port]` without credentials/path/query/fragment; token file contains 64 lowercase hex characters with optional final newline |
| `--action-cooldown` | `serve` | 30 seconds; finite and at least 1 |
| `--allow-maintenance`, `--trust-proxy-user` | `serve` | Off; require `--allow-actions` |
| `--audit-read-token-file` | `serve` | Off; requires explicit audit path and a distinct read secret |
| `--notification-token-file` | `serve` | Off; private notification ingestion secret |

Keep one persistent state path per Docker host/job. A busy state lock fails
immediately with 117. See [cron](cron.md), [trigger delay](trigger-delay.md),
[restart limits](restart-limit.md) and [serve](serve.md) for policy and token details.

## Scenario fields and overrides

Scenario mode defaults to `cron`. All modes allow `mode`, `description`,
`select` or `all: true`, `client`, `client-from-env`, `event-window`, `audit-file`,
`audit-max-bytes`, `audit-files`. Selection fields are `name`, `id`, `image`,
`label`, `group`, `status`, each a string or nonempty string list. Choose `select`
or `all: true`, exclusively. Group selection allows only an additional status.

| Mode | Additional fields |
| --- | --- |
| `stats` | `resources`, `output` |
| `cron` | `rules`, `state-file`, `cooldown`, `max-restarts`, `trigger-after`, `max-gap`, `dry-run` |
| `serve` | All cron fields plus `resources`, `bind`, `port`, `interval`, `stale-after` |

`resources` and `rules` accept a string or nonempty string list. Booleans must be
YAML booleans, not strings. Integer fields accept integers or integer strings;
other numeric fields accept finite numbers or numeric strings, including template
results, then apply CLI bounds. Unknown or wrong-mode fields are rejected.

`run`/`scenario` reject global selectors and Docker-client overrides. Configuration,
log and runtime paths are inherited; global audit/event-window settings apply
unless the scenario defines them. CLI `run --dry-run` only enables simulation.
The configuration is rendered once and passed to the runner. Scenario names do
not identify cooldowns: changing the state path starts separate policy history.
Manual HTTP action options and exec-exit propagation are not scenario fields.

Since 0.0.77, access resources in stats/serve scenarios require the directory
group's `access` identity during offline validation, just as access rules and
execution do. Disk/inode/mount-mode resources do not require it.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Successful command; can mean no rule matched or all actions were skipped |
| 2 | Argument parsing/validation error, before command execution |
| 110 | Configuration, alias, scenario or value error handled as such |
| 114 | No container selected |
| 115 | Required observation unavailable |
| 116 | Action failure; default for failed in-container exec |
| 117 | Persistent state lock busy |
| 118 | State read/write/validation failure |
| 119 | Audit error, or handled OS/value errors in local administration commands |
| 140 | Rule syntax error reaching the historical runtime CLI |
| 150 | Unexpected exception reaching the historical runtime CLI |
| 170 | Docker SDK connection/client error |
| 180 | Docker API error during collection; action API failures normally become 116 |
| 255 | Interrupt/SystemExit handled inside the runtime CLI; not a promise for every signal or every entry point |

Some `monit --rsc` invocations intentionally return data as the process status:

- `status`: running 0, created 10, paused 20, restarting 30, removing 40, exited 50,
  dead 60.
- `health`: healthy 0, starting 10, unhealthy 20, none 30, unknown 115.
- One ordinary `*_percent` resource: integer truncated and bounded to 0..100.
  Raw `stats`/rule measurements are not capped. Filesystem percentages return
  per-path output instead.

These early exits describe the **first selected container**, not an aggregate.
Use an exact container selector for a process-status check. `--propagate-exit-code`
in `monit`/`cron` returns a failed completed exec's 1..255 status instead of 116;
it can overlap every other status family. Check command mode and diagnostics.

## Open decisions before 1.0

- Unify or explicitly retain strict validation versus permissive runtime loading.
  Normalize configuration failures that currently reach generic status 150.
- Decide whether an explicitly unknown client must fail even with no configured
  clients; the historical runtime currently falls back to the environment.
- Decide whether mixed group/direct selection should be rejected in all commands,
  as it already is in scenarios, rather than silently prioritizing groups.
- Decide how to support commas in direct regular-expression selectors without
  breaking existing comma-separated selection.
- Review the [historical chained-comparison operand order](architecture.md#compatibility-and-validation):
  `10 < cpu_percent < 90` currently compares the measurement against both bounds
  using `<`. Prefer an AND condition alias with `cpu_percent > 10` and
  `cpu_percent < 90`; do not reinterpret existing expressions without migration.
- Resolve Python support metadata: CI currently covers Python 3.10/3.12, while
  package metadata still advertises older interpreters that this code cannot run on.
- Define the supported Docker/platform matrix, deprecation process and migration
  policy; separately review HTTP fields, metrics and journal schema compatibility.

No internal Python class, diagnostic wording, implicit argparse abbreviation,
Docker enumeration order or undocumented YAML behavior is declared a stable API
by this baseline. Existing DWho, HTTPdis and Sonicprobe foundations remain in use.

## Verification

`test_cli_contract.py` covers target selection, label values/image tags, regular
expression boundaries, group precedence, import replacement, client precedence
and option placement. `test_scenarios.py` covers scenario overrides, offline
inspection, single rendering, state continuity and access-resource validation.
Existing `test_check_config.py`, `test_monit_docker.py`, `test_cron.py`,
`test_trigger_delay.py` and `test_restart_limit.py` cover diagnostics, exit-code
propagation and persistent policies. These tests support this inventory; they
do not complete every milestone in the 1.0 roadmap.
