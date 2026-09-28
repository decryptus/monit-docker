# monit-docker 1.0.0

This release establishes the documented configuration/CLI, HTTP `/v1`, metrics
and journal contracts. It retains the local YAML model and explicit CLI, cron,
serve and read-only TUI entry points. Running a noninteractive command never
opens curses. Internal Python classes and undocumented behavior are not public
interfaces. Future changes follow the [deprecation policy](deprecation-policy.md).

## Changes since 0.0.82

- Discover and execute the architecture tests with unittest, including installed
  package scans and explicit rejection of empty scans.
- Check test declarations against actual collection before source, package and
  image tests. A passing empty or truncated suite cannot validate a release.
- Limit Docker statistics response bodies to 2 MiB and five seconds of body
  reading, interrupt stalled sockets and bound samples without progression.
- Normalize invalid selector expressions to the documented CLI error instead of
  exposing the regex engine's exception.
- Restore terminal state on SIGHUP and SIGTERM and preserve noninteractive CLI
  behavior. Centralize exact identifier validation without changing selectors.
- Gate publication on regression, installed distributions, image/UI tests and
  upgrade/rollback rehearsals for the same revision.

## Upgrade and compatibility

Review the [0.0.80 beta changes](beta-decisions.md) when upgrading from 0.0.79 or
earlier: shared validation, unknown-client refusal, intersection of group/direct
selection, literal commas and Python-order chained comparisons remain in force.
No `/v2`, configuration service or new login for the local CLI is introduced.

Follow the [backup and rollback procedure](installation-upgrades.md). Preserve
configuration, state, journals and retained archives; stop writers before making
consistent backups. Do not reset restart budgets or replay historical actions.
Upgrade CI covers 0.0.78, 0.0.79 and 0.0.82. A successful historical run does not
validate a different candidate or an arbitrary older version.

Pin `monit-docker==1.0.0` or `decryptus/monit-docker:1.0.0`; the optional UI uses
`decryptus/monit-docker-ui:1.0.0`. Images target Linux amd64 and do not update the
`latest` tag. See [supported environments](supported-environments.md).

## Validation status and remaining limits

The release workflow must pass on the tagged revision. Its checks include the
398-case application suite, separately executed opt-in Docker/terminal tests,
desktop/mobile browser checks, documentation and published-version upgrade and
rollback exercises. Collection counts include tests whose prerequisites are
unavailable in an individual job; dedicated integration jobs remain necessary.
Workflow results and their artifacts are the evidence, not this list of checks.

Real SSH TUI acceptance was completed after publication on 2026-09-28 with the
published agent on Debian 13; see the [tested scope](terminal.md).

The following are not certified by a green CI run and remain explicit follow-up
items in the [roadmap](roadmap.md):

- Prolonged observation on representative hosts remains pending final review.
- Existing performance figures are finite reference measurements, not service
  level guarantees or a multi-hour soak across production workloads.
- Statistics body bounds are not an absolute deadline for the entire monitoring
  cycle or every action. Connection/header phases retain their own timeouts.
- The original intermittent OOM fixture restarted with exit 137 but no Docker
  OOM notification in either captured history query. A startup handshake alone
  did not remove the failure. The fixture now keeps its main process alive while
  a child exhausts memory, requires a raw Docker OOM event, then propagates the
  child's exit code to trigger an automatic restart. Both fresh agents must read
  the retained OOM after that restart. CI preserves diagnostics and runs six fresh
  fixtures. The exact upstream cause of missing notifications during immediate
  main-process death is not established. No OOM is inferred from exit 137 alone.
- Manual request deduplication is in memory; process crashes do not provide an
  exactly-once action guarantee. Journal failure cannot undo an external action.
- Physical storage-failure rehearsals and every Docker/cgroup/platform combination
  are outside the tested matrix. The Docker socket grants Docker privileges even
  when its bind mount or an interface is described as read-only.

Publication of versioned artifacts and deployment to a particular host are
separate operations. Deployment evidence must identify the actual image/version
and target and verify retained data and recovery there.
