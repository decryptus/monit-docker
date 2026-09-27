# Architecture and coding-contract review — 2026-09-27

Reviewed commit: `67837c48238c5f62e95e03432d9cc09cf2b5ef2c` on `master`.
Status: **M1/M2 corrected on this branch; M3/M4/M5 remain open follow-ups**.
The findings below describe the pinned original snapshot. The follow-up extracts
neutral JobOptions/scenario validation, composition, MonitoringApplication and
StateOperations. CLI classes retain input/output and transport startup only.
State and clock dependencies are injected; no shared service imports the CLI.
New AST and fresh-process tests exercise composition, cycles, manual cooldown,
maintenance expiry, restart rearm and dry-run with CLI imports blocked.

Local follow-up validation: **337 tests and 504 subtests passed**, including two
new architecture tests; 15 opt-in Docker integration tests were skipped.
CI results are recorded on the PR. No Docker daemon action
was performed locally. The HTTP credential/query separation and selector/style
findings below are deliberately tracked separately from the M1/M2 extraction.

## Scope and method

The maintainer requires independent business/application services with CLI, HTTP
and visual interfaces as clients. The review scanned all Python package imports,
including function-local imports, then traced scenario validation/execution,
cron/serve composition, manual actions, audit reads, selectors and HTTP routes.
The web UI's action availability was compared with server-side checks. This is
an architecture/validation review, not a complete security or capacity audit.

Sources were fetched at the pinned commit. Checks used fake collectors and
test HTTP servers; no Docker daemon operation or production change was made.

## Findings

### M1 — High: scenario validation and execution go through the CLI

[adapters/scenarios.py:126–188](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/adapters/scenarios.py#L126-L188)
imports `monit_docker.cli` inside `validate_scenario` and `run_scenario`.
`scenario_arguments` turns YAML into argument strings, validation calls
`cli.argv_parse_check`, and execution calls `cli.main`. The scenario adapter also
prints results/errors and defines an argparse subclass. `adapters.validation`
imports the scenario adapter, whose functions import validation helpers back.

Thus offline scenario validation needs the command interface, and application
execution cannot be reused independently of CLI dispatch. This is a Python call
chain, not a shell invocation or an API launching a terminal. Blocking the CLI
import reproduces failure for a valid minimal stats scenario before any Docker
connection.

**Correction:** parse YAML and CLI input independently into a validated neutral
scenario/request model. One application service resolves and runs that model.
Keep argument construction, output and exit-code mapping in the CLI adapter;
remove the validation/scenario circular dependency through shared neutral rules.

**Acceptance:** validate and execute a scenario with fake adapters while CLI
imports are blocked; compare behavior with normal CLI execution, including
selection, policies, dry-run and rendered configuration reuse.

### M2 — High: cron/serve application behavior is still owned by CLI classes

[cli.py:540–563](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/cli.py#L540-L563)
performs maintenance expiry and its audit lifecycle in `_rule_policy`.
[cli.py:688–753](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/cli.py#L688-L753)
owns the state-locked monitoring cycle, manual cooldown reservation, restart reset,
maintenance changes and Docker exception translation. The CLI creates
`MonitorService(self._cycle, ...)` and `ManualActions(self._manual_action, ...)`.

The service and HTTP modules do not import CLI directly, but their configured
callbacks still execute application logic implemented on CLI command objects.
A top-level import check therefore misses this coupling. Composition in a process
entry point is legitimate; maintenance and action-state semantics inside that
entry point are the part that needs extraction.

**Correction:** move cycle/manual-operation orchestration to an application service
with explicit state, engine, clock, audit and policy inputs. A neutral composition
module wires it to cron, serve and HTTP. CLI remains responsible for parsing,
presentation, process launch and exit status.

**Acceptance:** direct service and CLI paths give the same cooldown, maintenance,
restart-budget and audit results without constructing a CLI command object.

### M3 — Medium: business services carry HTTP credentials and wire semantics

[manual_actions.py:26–34](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/manual_actions.py#L26-L34)
requires/stores browser origin, proxy token and trusted-header mode in the action
queue. `NotificationAudit` and `AuditReader` also hold HTTP tokens. The HTTP
handler reaches into these objects to authenticate requests.
[audit_query.py:37–77](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/audit_query.py#L37-L77)
combines URL query parsing and HTTP-coded errors with reusable journal filtering;
`AuditReader.page` takes a raw query string.

This couples otherwise reusable operations to one transport and makes their
constructors carry irrelevant secrets for non-HTTP use. No credential disclosure
or authentication bypass is asserted by this finding.

**Correction:** keep secrets, origin/header checks, query decoding, export format
selection and HTTP status mapping in transport/auth adapters. Pass validated
filter values and caller identity to services. Keep action authorization,
selection/protection checks and cursor scope enforcement server-side.

**Acceptance:** action, notification and journal operations work with no HTTP
token or URL input, while existing transport authentication tests still pass.

### M4 — Medium: user regex selectors have no execution bound

[adapters/selection.py:37–41,54–62](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/adapters/selection.py#L37-L62)
compiles `~` expressions with Python `re` and evaluates them in the monitoring
process. There is no expression length/work/time bound in this adapter.

A selector `~(a+)+$` against the valid name `"a" * 30 + "b"` exceeded a one-second
deadline in an isolated subprocess; the review harness terminated that process.
This is a reproducible reliability risk for operator-provided configuration,
not a claim that untrusted HTTP callers can currently submit selectors. A stuck
selector prevents its monitoring cycle from progressing.

**Correction:** define a bounded selector implementation, explicit failure behavior
and limits, retaining the documented regex-prefix matching semantics. Merely
limiting pattern length or moving matching to a Python thread does not establish
a hard execution bound. Rejected/timed-out selectors must not select everything.

**Acceptance:** pathological input terminates within a defined budget; valid exact,
glob, regex and group selectors keep their documented behavior.

### M5 — Low: alias validation and fixed patterns are inconsistent

[adapters/validation.py:138–139](https://github.com/decryptus/monit-docker/blob/67837c48238c5f62e95e03432d9cc09cf2b5ef2c/monit_docker/adapters/validation.py#L138-L139)
uses inline `re.match('^...$', name)`. Python `$` can match before a final newline:
`CheckedConfiguration._entries({'invalid\n': {'exec': ['restart']}}, 'command')`
accepts that key. The complete `check_configuration` path later rejects the
command as an unknown rule alias, so this is an inconsistent intermediate
validator/diagnostic, not acceptance of an executable invalid configuration.

Fixed container-ID/token regexes are also repeated inline in `cli.py` and
`adapters/state.py`, while `manual_actions.py` has compiled constants; alias grammar
is repeated across syntax, validation and filesystem definitions. Some fixed field
sets/mappings remain inside methods. Routes in `http_routes.py` and many existing
module constants already follow the requested style.

**Correction:** neutral shared validation constants/functions, full matching for
identifiers, uppercase module constants for fixed schemas and mappings. Preserve
the existing project's alias grammar and selector contract; do not substitute
another project's naming convention. Dynamic result objects stay local.

**Acceptance:** newline, Unicode and length-boundary fixtures agree across relevant
entry points; existing CLI/API contract tests remain unchanged in meaning.

## Positive evidence and limits

- `domain/` and `core/` have no CLI, HTTP or Docker imports. The existing
  `test_core_and_domain_import_without_third_party_dependencies` passed using
  Python `-S`. The calculation/rule engine separation is real and worth preserving.
- HTTPdis routes are centralized in `adapters/http_routes.py`; Sonicprobe provides
  the worker infrastructure. Requests call services, not shell commands or UI.
- The web UI's disabled buttons are not the sole protection: `ManualActions`
  validates the request/selection/protection and `MonitoringEngine` reselects the
  exact container and checks current state before execution.
- Local YAML, cron/serve, separate optional UI and English stable error codes
  remain the intended product model. This review requires no Redis/Centrex change.

## Verification evidence

Targeted Python 3.12 suite: **96 passed, 1 skipped, 102 subtests passed** across
`test_core`, `test_engine`, `test_service`, `test_scenarios`, `test_check_config`,
`test_httpdis_transport` and `test_api_contract`. The skipped case requires example
files not downloaded into the review checkout; it is not counted as a pass.
Dependency warnings concern deprecated `cgi`/`crypt`. This is not a full Docker
integration or release validation run.

Additional checks: all-package AST imports, callback tracing, blocked-CLI scenario
validation, the bounded external regex reproduction, intermediate alias validation
and complete configuration rejection for that same alias.

Existing tests protect core independence but do not prevent M1/M2 at the
application layer. `AGENTS.md` records requirements; automatic enforcement still
needs to accompany the refactor, not be claimed from documentation alone.

## Required correction order

1. Extract neutral scenario/request validation and application orchestration
   (M1/M2), with import-blocking and direct-service parity tests.
2. Separate transport/auth configuration from action/notification/journal services
   (M3), retaining the HTTP contract and server-side checks.
3. Bound selectors and centralize validation/constants (M4/M5), with regression
   cases for timeout, matching semantics and newline boundaries.
4. Extend architecture enforcement beyond core/domain, including lazy calls and
   callback ownership. Update `architecture.md` to describe the corrected layers.
