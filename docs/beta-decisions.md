# Beta behavior cleanup — approved 2026-09-27

Status: **introduced in 0.0.80**, following 0.0.79. This is an explicit maintainer-approved
beta adjustment, not a frozen 1.0 contract or an additional API version. The
maintainer chose to correct these behaviors now rather than preserve accidental
semantics through an extra notice release. No deployment or data-upgrade rehearsal
is claimed by this document.

## Configuration and target selection

- All monitoring modes and scenario inspection/execution validate the entire
  YAML configuration, including dormant aliases and scenarios, before Docker
  access. Unknown sections/fields, malformed imports and invalid values fail
  with configuration error 110. Missing configuration remains permitted for simple
  CLI-only monitoring; `check-config`, `scenario` and `run` still require a source.
- A named but unknown Docker client fails, including when `--client-from-env` is
  supplied. Omitting `--client` retains normal default selection; a valid name
  with explicit `--client-from-env` still uses the environment.
- Selected groups form a union. Direct name/ID/image/label patterns form another
  union. If both are present, their intersection is selected; status is an
  additional filter. This is identical in CLI and YAML scenarios.
- Commas never split selector values. Replace `--name 'web-*,api-*'` with
  `--name 'web-*' --name 'api-*'`, or YAML `name: ['web-*', 'api-*']`.
  A regex such as `~worker-[0-9]{1,3}$` is now preserved directly.
  Membership conditions such as `status in (running,paused)` are unaffected.
- Selector regexes retain Python syntax checking and start matching. Bounded
  matching uses the `regex` dependency (VERSION0), with 50 ms per comparison and
  a 4096-character pattern limit. Timeout or rejection is an error, not an
  unrestricted selection. This does not bound total monitoring-cycle duration.

Example: `--ctn-group production --name 'web-*'` now chooses web containers in
production. Previously the direct name filter was ignored. Removing the direct
filter restores group-only selection if that is what the operator intended.

## Chained comparisons

`10 < cpu_percent < 90` now means `10 < cpu_percent and cpu_percent < 90`, as in
Python. Previously the first comparison was reversed (`cpu_percent < 10`). This
bug was reproduced from the first available 2019 commit and from 0.0.50 (2023).
The fix applies to inline and aliased conditions, supported numerical resources
and byte-unit bounds. Equality, inequality, inclusive/exclusive bounds and
ascending/descending comparisons keep their written operand order.

Review existing chained rules: their matching set may change. To intentionally
preserve the former example's behavior, use a condition alias containing
`cpu_percent < 10` and `cpu_percent < 90`. Do not assume the fix merely changes
presentation: it can change which actions execute.

## HTTP

- `/v1` stays in place; there is no `/v2` or new authentication mechanism.
- Actions and notifications both accept a single JSON content-type header with
  optional parameters. The body remains UTF-8; duplicate headers are rejected.
- Supported but incorrect methods on active known routes return 405 and `Allow`.
- Unknown/noncanonical paths and disabled protected capabilities return 404.
  Trailing slashes, duplicate path separators and encoded path aliases are not
  supported aliases. OPTIONS remains disabled; no CORS endpoint is added.
- Existing authentication, body limits and action authorization remain enforced.

Clients that expect 404 for GET on an active `/v1/actions` must handle 405. Clients
that expect 405 for disabled actions/notifications must now handle 404. API field
names/types and persisted data schemas are unchanged by this cleanup.

## Deployment review

Before applying this beta version, run `check-config` with the new version against
copies of each configuration; review group/direct combinations, comma-separated
selectors and chained expressions. Use `stats` to inspect target selection, then
`--dry-run` for rules before enabling actions. Fix dormant invalid entries too.

Retain the previous image/package and configuration. A rollback to 0.0.79 restores
its old selection/comparison behavior, so restore/review matching configuration as
well. This change does not rewrite state or audit data, reset counters or replay
actions. Cross-version backup/rollback rehearsals remain a separate roadmap task.

## Scope and verification

Keep standalone local YAML, existing DWho/HTTPdis/Sonicprobe foundations, and no
extra login for local CLI usage. Centrex and its proposed shared library remain
outside this release.

Regression coverage compares every supported comparison-operator pair against
Python, tests unit bounds/aliases, selection intersection and comma preservation,
strict offline/runtime validation, unknown clients, regex failure bounds, HTTP
methods/paths/content types and application composition without CLI imports.
Docker integration and deployment checks remain required CI/release evidence;
local fake-adapter tests do not substitute for real Docker tests.
