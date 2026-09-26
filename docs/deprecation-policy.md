# Deprecation and migration policy

**Status: policy for future changes in the 0.0.x series.** Publishing this policy
neither deprecates an existing feature nor approves the 1.0 compatibility contract.
The [roadmap](roadmap.md) retains its open decisions and upgrade rehearsals.
Application versions, HTTP route versions and persisted data schemas are separate
identifiers: changing one does not automatically change the others.

## Scope and compatibility review

Review changes against the documented [configuration and CLI](config-cli-contract.md),
[HTTP API](http-api-contract.md), [metrics](metrics.md),
[journal](journal-compatibility.md) and [environment](supported-environments.md)
baselines. A 0.0.x version increment alone does not imply compatibility.

Treat the following as potentially incompatible, even when described as a fix:

- Removing or renaming options, YAML fields, scenarios, routes, fields or metrics.
- Changing defaults, selector/rule interpretation, action ordering, authentication,
  exit codes, HTTP errors, field types, units, labels or machine-readable reasons.
- Changing stored encoding/meaning, supported schemas, retention defaults, or the
  Python/Docker/platform prerequisites needed by an existing deployment.

Adding an optional field or value still needs a consumer review: strict parsers,
CSV projections, enum handling and metric cardinality can make an addition break
an integration. Human diagnostic wording, internal Python classes, undocumented
behavior and implicit CLI abbreviations are not stable interfaces. Correcting
those does not require the ordinary notice period, but a known operational impact
must still be explained. English machine-readable reasons belong to the review;
they are not interchangeable with human diagnostic text.

## Ordinary deprecation sequence

1. Record the old behavior, affected users, replacement and compatibility risks in
   the change's pull request. Link the relevant baseline and tracking issue or PR.
2. Publish the replacement and a deprecation notice in at least one **released
   0.0.x version** while the old documented behavior remains usable. A notice only
   on the development branch is insufficient. Document both forms with examples.
3. Name the announcing release and the earliest removal release in the notice.
   Removal must be in a later release, never the announcing release. There is no
   calendar-based support window or automatic removal when a date passes. If the
   removal release is undecided, say so and announce it before removal.
4. Before removal, verify the migration on the explicitly supported source/target
   versions, update the baselines and examples, and publish the actual change and
   rollback limits in the removing release's notes. The maintainer reviews the
   evidence; passing the notice period alone does not justify removal.

Notices belong in the affected guide and published release notes (linked from the
release's PR). They must be discoverable without parsing runtime logs. Runtime
warnings may supplement them, but must not corrupt structured stdout, HTTP bodies
or journal records. This policy adds no warning mechanism by itself.

Pin exact versions in deployments and read all intervening notices when skipping
releases. This policy does not promise indefinite support or backports for every
0.0.x release; each incompatible change identifies its tested upgrade paths.

## Urgent exceptions

A confirmed security exposure or risk of data loss can require an immediate
incompatible correction. The maintainer must explicitly record why keeping the
old behavior for a notice release is unsafe, the affected versions, the impact,
the available replacement or recovery procedure, and any rollback restriction.
Publish that exception with the corrected release; do not label it compatible.
Where exploit details must be withheld, still document the operational impact.
Convenience, cleanup or a generic “bug fix” label do not waive the normal sequence.

## Migration and rollback requirements

Configuration and command changes need before/after examples, validation steps,
and a statement of changed defaults and action selection. Do not reinterpret an
old rule silently. In particular, the open chained-comparison and comma-selector
decisions remain unresolved until a separately reviewed change applies this policy.

For HTTP or metric changes, list affected consumers and the coexistence/versioning
strategy. A `/v1` route name is not permission to change field meaning silently.
Whether a particular change needs another route version remains an explicit
contract decision; this policy does not introduce `/v2`.

For persistent data, publish a reader/writer compatibility table with exact
application versions and schemas, mixed-history tests, backup instructions and
rollback boundaries **before enabling an incompatible writer**. Prefer lossless
read-time adaptation where possible. Any required physical conversion must be
explicit, preserve original bytes, write separately, support a dry run, verify
counts/identities and document interruption recovery. Unknown formats must fail
explicitly instead of being guessed or discarded.

The existing [audit-migrate command](audit-migration.md) is an optional journal
schema 1/2-to-2 converter. It does not migrate configuration or action state and
does not replace the live journal. Its availability is not proof that an arbitrary
upgrade or downgrade is safe.

A deployment procedure must identify all persistent paths and writers, stop writers
for a consistent backup, preserve permissions and test the target against copies.
Verify collection, state, history and exports before resuming normal operation.
Rollback must first preserve post-upgrade data separately and verify the older
reader/configuration against copies. Restoring a backup can exclude later events;
state this explicitly. Never replay actions merely because records were migrated.
If rollback cannot preserve newer data, document that limit and the recovery path.
A same-version copy test does not certify a cross-version deployment upgrade.

## Required change notice

Use concrete versions when publishing; the placeholders below are a template,
not a scheduled deprecation:

```text
Affected interface and old behavior:
Reason for the change:
Notice/replacement release:
Earliest removal release (or not yet scheduled):
Affected and tested source -> target versions:
Replacement and before/after example:
Validation and migration commands:
Persistent data, backup and interruption recovery:
Rollback limits and treatment of post-upgrade data:
Regression/upgrade evidence and known limits:
Tracking issue or pull request:
Urgent exception justification (if applicable):
```

If a field is inapplicable, explain why. Keep notices and machine-facing examples
in English; the website also provides a French explanation. Approval of the 1.0
contract will require a separate review of the versioning and support commitment.
