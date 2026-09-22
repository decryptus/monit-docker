# Validate configuration before running

`check-config` validates the configuration used by simple mode, cron and serve.
It uses their existing YAML/Mako loader, selector compiler and rule parser without
connecting to Docker or executing container actions. It creates no agent log,
runtime directory, lock or state file.

This command is introduced after v0.0.60. Until its release, use a checkout that
contains the command (`python -m monit_docker` from that checkout).

## Usage

Global configuration and selection options go **before** `check-config`:

```sh
monit-docker -c /etc/monit-docker/monit-docker.yml check-config
monit-docker -c /etc/monit-docker/monit-docker.yml --ctn-group web \
  check-config --cmd-if 'mem_percent > 90 ? restart' --output json
```

Add repeated `--cmd` or `--cmd-if` options to check the expressions you intend to
pass to `monit`, `cron` or `serve`. These options validate only; they do not run
commands. All configured command aliases, condition aliases and container groups
are checked even if the supplied expressions/selectors do not use them.

To validate the repository example, including its relative imports:

```sh
python -m monit_docker -c etc/monit-docker/monit-docker.yml.example check-config
```

The named file takes precedence over `MONIT_DOCKER_CONFIG`. If that file does not
exist, the command uses the inline environment configuration when set, matching
the runtime's precedence. If neither exists, validation fails explicitly.
An empty mapping (`{}`) is valid; an empty file, `null` or a list is not.

## What gets checked

- Top-level sections and entry shapes: `general`, `vars`, `clients`, `ctn-groups`,
  `conditions` and `commands`; missing fields and unknown sections/entry fields.
- Existing import directives, relative file paths and Mako rendering. The same
  import scope and override behavior as the runtime is used.
- Client configuration mappings and basic TLS shape; explicit client names.
  `--client-from-env` overrides `--client` as it does at runtime.
- Every group match and regular expression, CLI selectors and selected group names.
- Every command/condition alias, supported Docker action, action argument shape,
  rule syntax, resource name, unit conversion and condition value conversion.

Validation stops at the first error. Locations identify the source/import file,
entry or supplied rule; YAML syntax errors also include line and column numbers.
Messages avoid dumping rendered configuration, command contents and credentials.
Fix the reported error and rerun to reveal any later errors.

Checks are deliberately stricter than the legacy loader, which can ignore some
unused sections or fields. The behavior and exit codes of the existing commands
are unchanged.

## Output and exit codes

Text is the default. `--output json` writes one result object to standard output:

```json
{"valid": true, "errors": [], "summary": {"clients": 1, "groups": 1, "commands": 2, "conditions": 1, "rules": 1}}
```

`summary` counts loaded entries and supplied rules. On failure it is empty and
`errors` contains the first diagnostic with `location` and `message` fields.
Exit codes are `0` for valid configuration, `110` for a configuration/rule error,
and `2` for invalid CLI arguments. This makes the command suitable for deployment
scripts and CI. Standard parser diagnostics may also appear on standard error.

## Limits

A valid result does not prove Docker is reachable, TLS credentials are readable,
a Docker SDK option is supported, a container exists, or an in-container command
will succeed. It also does not predict whether conditions will match live values.
Docker-specific connection settings and action keyword options are not exhaustively
validated against daemon/SDK versions. Use a monitored test run or the existing
[dry-run mode](cron.md) for checks requiring actual container observations.

Configuration and imported **Mako templates must be trusted**: templates execute
Python while being rendered, just as they do during normal startup. Offline here
means the validator itself does not call Docker or execute rule actions; it is
not a sandbox for untrusted templates.
