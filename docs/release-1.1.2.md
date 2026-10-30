# monit-docker 1.1.2

Maintenance release for command-line rule errors, with the Compose quickstart
merged since 1.1.1.

## Changes

- Unsupported size units such as `100 mb` now report a rule syntax error
  (exit status **140**) without a Python traceback. Use supported units such as
  `MB`, `MiB`, `kB` or `B`.
- Literals that cannot be converted to the measured value's type, such as `1.5`
  for integer-valued `mem_usage`, now report a value error (exit status **110**)
  instead of the unexpected-exception status 150.
- The root Docker Compose installation now starts an observation-only agent.
  An optional authenticated HTTPS UI and a reproducible memory tutorial are
  available. No automatic action is enabled by the default configuration.
- Contributor tooling uses Pillow 13 and verifies all 19 branding exports on
  Python 3.11 and 3.12. Pillow remains development-only; the agent still supports
  Python 3.10+ and its runtime requirements are unchanged by this update.

## Install or upgrade

```sh
python -m pip install --upgrade 'monit-docker==1.1.2'
# Optional Textual interface:
python -m pip install --upgrade 'monit-docker[textual]==1.1.2'
```

Versioned Docker images are `decryptus/monit-docker:1.1.2` and
`decryptus/monit-docker-ui:1.1.2` for Linux amd64. The `latest` image tag is not
updated; select the version explicitly.

The same tested images are also published as `ghcr.io/decryptus/monit-docker:1.1.2`
and `ghcr.io/decryptus/monit-docker-ui:1.1.2`.

No configuration, HTTP API or journal schema migration is required from 1.1.1.
Preserve configuration, state, restart budgets and journals when upgrading.
Scripts interpreting malformed-rule exit status 150 must accept the documented
140/110 statuses instead.

**Existing root Compose users:** the historical cron/PHP-FPM example is replaced
by observation-only `serve`. Preserve and review your existing Compose and cron
configuration before adopting this entry point: old `MONIT_DOCKER_CRONS` jobs do
not run automatically in the new setup. See the [Compose quickstart](compose-quickstart.md)
([français](compose-quickstart-fr.md)) and [memory tutorial](tutorial-memory.md).

The existing [compatibility contract](config-cli-contract.md) and
[upgrade guidance](installation-upgrades.md) continue to apply. Automated release
checks do not replace validation of your deployment or prolonged field observation.

Contributions: [#90](https://github.com/decryptus/monit-docker/pull/90),
[#92](https://github.com/decryptus/monit-docker/pull/92) and
[#93](https://github.com/decryptus/monit-docker/pull/93).
