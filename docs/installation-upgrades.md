# Installation, upgrade and rollback rehearsals

The `Installation and upgrade rehearsals` workflow tests published **0.0.78,
0.0.79 and 0.0.82** against the candidate commit. These are explicit rehearsal baselines,
not a promise that every older version is supported. The candidate has not been
published merely because these checks pass. Consult the workflow run and its
artifacts for actual results; an added test is not evidence of an executed test.

## Reproduce on a disposable Linux machine

Use Python 3.12, Docker with its local Unix socket, and enough space for three
agent installations/images. The integration checks create and remove uniquely
named test containers and execute commands inside those containers. Run on a
disposable daemon, not a production host. No real configuration is needed.

```sh
docker pull alpine:3.20
python .github/scripts/check-upgrades.py --backend pip --integration --output /tmp/upgrade-pip
python .github/scripts/check-upgrades.py --backend docker --integration --output /tmp/upgrade-docker
```

Without `--integration`, the pip backend needs no Docker and checks storage and
configuration offline. The Docker backend always requires the local Docker socket.
The workflow also runs the existing published UI acceptance test at 0.0.79,
covering authenticated read-only/actions modes and desktop/mobile Chromium.

Each backend installs published baseline artifacts and builds the candidate from
the checkout. Packages run in separate virtual environments; images run with an
explicit Python entry point. Tests run outside the source checkout. Published
versions are checked against package metadata. Pip installation reports record
artifact URLs/hashes and dependencies; Docker image IDs identify the exact images
used. The report includes the candidate commit, runtime, phases and failures.
Dependencies are resolved at execution time, so keep these reports with releases.

When running an image with a numeric `--user` absent from its passwd database,
provide `LOGNAME`/`USER` explicitly. The 0.0.79 CLI uses `getpass.getuser()` for
action attribution and fails without either a resolvable account or that environment.
The rehearsal sets `LOGNAME=upgrade-rehearsal` and preserves the host UID/GID for
its disposable mounted files. This is attribution, not authentication.

For each baseline, the old implementation creates persistent state with a
cooldown, observation streak, restart attempt and maintenance deadline, plus
archived schema 1/current schema 2 events and a YAML configuration. The candidate:

1. Loads all values and exports the journal without changing persisted bytes.
2. Advances the restart budget to its limit and verifies the next attempt is refused.
3. Appends an event while preserving all older records and unrelated state.
4. Hands the updated data to the old version for a rollback read.
5. Verifies that an untouched backup remains readable and byte-identical.

Integration additionally runs the installed releases/candidate in real cron and
serve processes: dry-run, one executed command, cooldown across processes, ready
status, metrics and graceful SIGTERM. These isolated storage and execution checks
do not certify every production rule/configuration, a complete in-place package
manager upgrade, cross-platform deployments, or rollback to versions before 0.0.78.
The published UI check is a fresh installation test, not an upgrade of UI sessions.

## Operator procedure

Before upgrading, review [beta behavior changes](beta-decisions.md). In particular,
selection intersections, literal commas and corrected comparison chains may change
which containers receive actions. Validate the complete YAML with the candidate's
`check-config`, inspect selection with `stats`, then rehearse rules with `--dry-run`.

1. Record the current package version/dependency lock or image digest, service
   command, configuration, retention settings and file ownership.
2. Stop cron scheduling, the serve agent and **all** journal writers, including
   notification adapters. Confirm no process still writes to state/history.
3. Copy the complete configuration/imports, state file, journal and all retained
   numbered archives to a separate protected backup. Record SHA-256 checksums.
   Keep the original installation/image available. Do not reset restart counters.
4. Validate a copy with the new version. Keep journal retention limits unchanged;
   older large archives may require a larger read bound. Physical journal
   conversion is optional; see [audit migration](audit-migration.md).
5. Start a single agent with the same persistent paths and ownership. Verify
   readiness, selected containers, old history and restart/maintenance state
   before restoring other writers and cron scheduling.

For rollback, first stop all writers again and archive the **current** configuration,
state and journal, including events written since the upgrade. Prefer reusing this
current data only when the old release has been tested to read its schemas. Do not
silently discard new history or reset consumed restart budgets by restoring an old
backup. If schema changes force restoration, keep both copies and reconcile the
operational state before re-enabling actions. Never replay historical actions.

The current rehearsal checks direct readability of updated data by 0.0.78/0.0.79/0.0.82;
it does not establish a downgrade path for unknown future schemas. Failed schema
validation must remain an explicit stop. A backup read check does not by itself
prove that restoring it is operationally safe.

## Release gate

The publication workflow calls both regression and installation/upgrade workflows
for the same revision. Publication requires those jobs, installed-package checks,
image tests and UI tests to succeed. Upgrade rehearsals now include published
0.0.82 alongside 0.0.78 and 0.0.79; a historical successful run does not validate a
new candidate. Reusable workflows also run for pull requests without publishing.
