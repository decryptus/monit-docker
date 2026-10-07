# Engineering requirements

These are maintainer requirements, not optional preferences. Existing violations
are debt to remove, not examples to copy. See
[the architecture review](docs/architecture-review-2026-09-27.md) and
[the architecture contract](docs/architecture.md).

## Dependencies and responsibilities

- Domain and core must remain independent of CLI, HTTP, visual interfaces,
  formatting and Docker SDK objects. Application services must also be callable
  without CLI argument parsing or CLI command classes.
- CLI and HTTP are peer interfaces over the same application services. The web UI
  consumes the API. Business behavior must not import `cli`, build artificial
  command-line arguments, call CLI entry points or rely on CLI-owned callbacks.
- A neutral composition module wires collectors, executors, state, policies,
  scheduler and transport. The CLI parses arguments and presents results; it
  must not own maintenance, restart-budget or action execution semantics.
- Inject adapters through explicit contracts. HTTP origins, proxy secrets,
  headers, URL query parsing and status codes belong to transport/auth adapters,
  not monitoring/action/journal services. Services receive validated requests and
  identity/policy data; they return domain results and errors.
- UI checks can improve feedback but are not authorization or business policy.
  The server must independently validate and enforce operations.
- Review lazy imports, callback ownership and package initializers. An import-only
  scan is insufficient; moving code without separating responsibilities is too.

## Style and validation

- Reuse DWho, HTTPdis and Sonicprobe capabilities where applicable. Keep the
  existing HTTPdis routes and Sonicprobe worker infrastructure.
- Declare fixed routes, registries, schemas, enum choices, field sets, limits and
  regexes as named uppercase module constants; use a leading underscore for
  private constants. Dynamic result dictionaries remain local.
- Centralize each identifier/grammar contract in a neutral module, compile fixed
  regexes once, and use full matching for identifier validation. Do not change
  existing public selector semantics as an incidental style cleanup.
- Treat exact identifiers, globs and regexes separately. Bound dynamic regex work
  and reject invalid/pathological input explicitly; never turn a rejected filter
  into an unrestricted action. Preserve documented selection semantics.
- Generated `reason`, `message` and other product text must be English, with
  stable machine-readable codes. Keep presentation separate from raw values.

## Product constraints

- Keep the standalone local YAML configuration model. Do not introduce Redis or
  Centrex dependencies into the local agent without an explicit product change.
- Preserve the common engine for cron and serve and the optional separate web UI.
- Respect documented CLI/API contracts and state behavior. Preview status is not
  a reason to invent another API version or an unnecessary migration.
- Avoid needless disk writes and repeated production logs; retain the configured
  state/audit durability guarantees.

## Review and verification

- Check architecture independently of functional test results. Existing core
  import isolation coverage must be extended to application services and scenario
  validation/execution when these are extracted from the CLI.
- Exercise service initialization and callbacks with CLI/visual imports blocked.
  Test parity between CLI and direct service use with fake adapters, without
  Docker actions. Keep transport contract tests for the HTTP boundary.
- Cover identifier newline/Unicode/length cases and bounded dynamic regex behavior
  when changing validation. Do not impose another project's naming grammar here.
- Report confirmed violations and remaining limits; documentation alone is not
  an automated architecture gate.

## Test discovery and execution

- Verify the actual runner and every discovery root before adding or changing
  tests. A green command does not prove that all test declarations were loaded.
  Do not put standalone pytest functions into a suite run only by unittest.
- Run `.github/scripts/check-test-collection.py` with Python, the same
  interpreter/environment as the tests, and the declared runner. Pass separately
  discovered directories together when they are nested. The guard compares
  declarations in `test*.py` files with actual collection; it does not run tests.
- Reject empty suites, import/collection errors, duplicate definitions and
  declarations omitted by the runner. Keep test helpers out of the `test*`
  namespace. Explain intentional skips and separate integration prerequisites;
  never hide failures with `continue-on-error` or `|| true`.
- Run the normal test command after the guard. Report collected/executed/skipped
  counts and investigate unexpected changes; collection alone is not a passing
  test run. Parameterization may produce several cases per declaration.
- Check test paths, naming patterns, selection filters and CI commands together.
  A new test directory or non-Python test harness needs an explicit CI entry;
  this guard only covers the directories and Python naming pattern passed to it.
- Verify installed-package tests outside the source checkout where applicable.
  Architecture scans must resolve the package under test and reject empty scans.
- Preserve supported interpreter matrices. Changing runners requires an explicit
  decision and collection parity; this Python 3.8+ CI helper does not replace
  legacy-interpreter execution or integration/system acceptance tests.

Current project runner: `unittest` for `tests`. CI helper tests use
`unittest` in `.github/tests`.

## Copyright review

- Review project-owned copyright notices and packaging/documentation metadata
  when preparing a release. Keep the original start year and attribution, and
  update the end year for maintained material to reflect actual project work.
- Keep the setup metadata and generated documentation consistent. Do not alter
  third-party copyright notices or the dates in the standard license text.

## Separate user and contributor documentation

- Maintain two distinct entry points and tables of contents: user documentation
  for installation, configuration, operation, public APIs and troubleshooting;
  contributor documentation for architecture, internals, tests, benchmarks,
  release engineering and development plans.
- Keep README and package descriptions focused on users. Link to the contributor
  guide instead of embedding maintainer procedures. Library API examples belong
  in the user guide when they are needed to integrate the library.
- Keep registry publishing, CI setup, repository secrets and maintainer account
  configuration out of user guides, website manuals and package descriptions.
  Never include credential values or private infrastructure evidence in either guide.
- Put implementation reviews and acceptance records under the contributor
  navigation. Preserve user-facing compatibility limits, migration instructions,
  security requirements and failure semantics in the user documentation.
- Apply this separation to generated documentation and FR/EN website content.
  Update source content and generators together; do not patch only generated HTML.
- Before delivery, inspect both entry points, check links and build documentation
  with warnings treated as errors where supported. Review README/package text and
  the deployed manual for accidental maintainer content. Preserve private-project
  visibility and existing review/publication approval requirements.

## Optional Textual presentation

- Reuse `dwho.tui.textual` for shared layout, widgets and visual states. Keep
  collection, permissions, rules and execution in application/client services.
- Maintain the separate `textual_tests` unittest discovery root and optional CI
  job. Base commands must work without Textual/Rich imports.
- Generate candidate captures with `scripts/capture_textual.py`; keep synthetic
  fixtures labelled and record source hashes. Publish shared website captures
  only after the corresponding interface and source revision are accepted.
