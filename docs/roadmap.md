# Roadmap and validation record

## Current delivery — 22 November 2026

Monit-Docker **2.0.2 is published**. The 2.0 release is complete; it must not be
listed as a future milestone. See [2.0.0](release-2.0.0.md),
[2.0.1](release-2.0.1.md) and [2.0.2](release-2.0.2.md) for their immutable scope.

- Delivered: optional SQLite accounts/sessions, branded web authentication,
  optional read-only incident assistance, account profile/password/recovery-code
  operations and the optional remote-notification worker.
- Delivered in 2.0.2: email syntax for new account identifiers, compatible legacy
  accounts and a startup count warning for accounts without email identifiers.
- Still unverified: real-model usefulness/provider compatibility and physical
  notification receipt. Automated tests do not establish these results.
- Still incomplete: translation of the whole web dashboard. Authentication and
  AI already have French/English catalogs; other views need the same treatment.

### Immediate priority: finish the bilingual web interface

This is the maintainer's first development priority. Reuse self-hosted i18next
and separate FR/EN catalogs throughout the dashboard; do not introduce inline
translations or language conditionals.

- [ ] Inventory remaining text in overview/detail views, filters, menus, charts,
  history, action dialogs, loading/empty/offline states and error messages.
- [ ] Translate user-facing labels, confirmations, validation and accessibility
  text with stable keys, pluralization, named interpolation and locale formatting.
  Preserve original workload identifiers, raw logs and API codes.
- [ ] Preserve the language selected before login and across navigation/reload;
  verify device-language selection and the documented fallback.
- [ ] Validate key parity and language switching on desktop and mobile browsers,
  including anonymous demo and authenticated workflows.
- [ ] Update affected FR/EN guides/captures, then deliver matching agent/UI assets
  through the existing release and deployment checks.

### Next development priority: Docker Storage Monitor (proposed)

After completing the bilingual web dashboard, prioritize a read-only Docker
storage monitoring module for the standalone Community agent. This addresses
observed staging-disk pressure during deployment and backups without treating
host cleanup as a monitoring side effect. **Not implemented, released, deployed,
or enabled**; acceptance and compatibility review are still required.

- [ ] Audit the existing disk/inode/volume checks, Docker API access via the
  filtered socket, Prometheus/Grafana metrics, and DWho/HTTPdis/Sonicprobe
  capabilities before adding a storage collector. Keep CLI, HTTP, web and
  scheduled monitoring on the same application service.
- [ ] Report bounded host filesystem space/inode pressure and Docker's aggregate
  image, writable-layer, container, volume and build-cache usage. Distinguish
  active, unused and *potentially* reclaimable storage; Docker's reported
  reclaimable bytes are an estimate, not a promise of safely removable files.
- [ ] Provide per-container writable-layer usage and age for stopped containers
  only when the local Docker API and permissions support it. Separate shared
  image layers from unique size to avoid double counting. Identify missing
  privileges, unsupported storage drivers and unavailable metrics explicitly.
- [ ] Expose historical trends, configurable threshold alerts, and concise
  operator diagnostics in the FR/EN web interface, CLI and Prometheus where
  appropriate. Avoid unbounded scans or storing per-file inventories. Use
  rate-limited collection and bounded cardinality; do not block regular health
  monitoring when the storage collector fails.
- [ ] Document independent deployment-staging inspections and the separate
  host-maintenance jobs for Demo and Honorata. These remain host-specific
  GitHub Actions/cron responsibilities, not implicit Docker data managed by
  the open-source agent. Detect stale successful and failed deployment staging,
  but preserve active jobs and recovery/rollback evidence.
- [ ] Develop a generic **CI/CD supervision mode** for persistent self-hosted
  runner servers, regardless of CI platform. GitLab Runner and GitHub Actions
  self-hosted runners are integration examples, not names of product features
  or required dependencies. No platform token, vendor cloud, Centrex service
  or expanded Docker privileges are required for host-level monitoring.
- [ ] Monitor transient job-container churn, stopped/abandoned containers,
  image/build-cache growth, writable layers, CPU/RAM pressure, host disk space,
  inodes and recovery after runner failures. Distinguish active jobs from
  inactive resources using supported runtime metadata; never assume stopped
  containers or unused images are safe to delete.
- [ ] Include the standalone Monit-Docker agent in the default provisioning
  baseline for persistent CI/CD runner hosts, rather than launching it in each
  short-lived job. Qualify startup, restart/reboot recovery, local alerts,
  monitored Docker access and upgrades on representative runner hosts.
- [ ] Reuse existing Monit-Docker authorized actions for an optional,
  platform-independent incident response: alert on a runaway or wedged job
  container, correlate the specific container/job and offer a reviewed stop
  or restart where supported. Never kill a legitimate active job or remove
  its image based solely on resource pressure. Keep protected targets,
  scoped grants, maintenance windows, cooldowns, budgets and audit enforced
  at the server. Automatic remediation requires a separately enabled policy
  with fresh confirmation that the job is no longer legitimately active.
- [ ] Document CI runner lifecycle and cleanup integration with platform
  examples (such as GitLab Docker executor) without coupling the agent to
  those platforms. Monitoring complements the runner's own cleanup and must
  preserve concurrent jobs, shared caches and rollback artifacts.
- [ ] Do not install a persistent agent inside ephemeral GitLab/GitHub CI
  jobs; install it on the long-lived runner host and keep per-job cleanup
  within the CI/CD workflow.
- [ ] Start with **read-only monitoring and alerts**. Any later cleanup is a
  separately reviewed, opt-in capability with a dry-run listing, exact object
  identity, explicit operator approval, revalidation, audit, maintenance/backup
  locks and protection of rollback image digests. Never use a broad
  `rm -rf *`, `docker system prune`, automatic volume pruning or deletion of
  Restic snapshots; exact-path `find` selection alone is not authorization.
- [ ] Test overlay2/containerd and alternate Docker storage drivers, image
  reuse/deduplication, stopped vs. running containers, changing Docker state,
  access denied/daemon unavailable, storage exhaustion, retention boundaries,
  security of filtered-socket permissions, alert deduplication and installation
  upgrade/rollback. Verify no unexpected Docker actions in read-only mode.

**Release order:** complete the bilingual dashboard first, then implement and
qualify Docker Storage Monitor; separately finish the already pending real-model
and physical push-notification acceptance work. Do not silently include a storage
cleanup daemon in the existing 2.0.2 release.

### Acceptance work for delivered capabilities

- [ ] Qualify real Ollama, OpenAI-compatible and Claude models on the documented
  incident scenarios in FR/EN; record accuracy, missing-evidence handling,
  latency, usage and actual cost where available. Keep analysis read-only.
- [ ] Validate opt-in remote notifications with a registered physical device:
  background/closed-app receipt, denial, reconnection and force-stop behavior.
  Check provider tickets/receipts separately from visible device receipt.
- [ ] Continue representative deployment observation, resource/latency
  measurements and failure/recovery checks described in the historical record.
- [ ] Reconcile remaining contributor reviews that still describe 2.0 features
  as unpublished; retain historical results without implying missing acceptance.

### Planned evolutions

These are backlog proposals, not implemented features, release commitments or
prerequisites for using the published standalone agent. After the bilingual UI,
prioritize them according to field feedback and the acceptance results above.

| Evolution | Intended result and boundaries |
| --- | --- |
| Verified email and self-service recovery | Verify mailbox ownership, add optional SMTP delivery and expiring single-use recovery links/codes with rate limits. Preserve administrator-issued recovery when email is not configured. Email syntax alone is not verification. |
| Alert lifecycle | Add server-owned acknowledgement, time-bounded silence and durable incident/open/recovered history, with authorization and audit. Keep the existing action journal and transport alerts working. |
| Notification preferences and diagnostics | Add quiet hours and per-account severity/channel preferences; provide an authenticated, rate-limited test to the user's own registered devices and readable delivery status. A test must not induce real workload failures or reveal tokens. |
| Extended history and charts | Extend navigation/comparison and bounded retention of existing snapshots/action history; define the API and measured storage limits before adding persistence. Preserve existing Prometheus/Grafana integration. |
| Bounded workload logs | Add authorized, size/time-limited container log reads with explicit access and disclosure rules. Do not automatically forward raw logs to AI providers. |
| Scenario groups | Implement the ordered, finite scenario groups specified below, preserving per-scenario protections, stop-on-failure behavior and existing CLI contracts. |
| Configuration-import hardening | Audit and bound section imports as specified below, with explicit compatibility/migration decisions before rejecting previously accepted configurations. |
| Operational diagnostics | Improve actionable configuration/connection/permission errors and reproducible support evidence; exclude credentials and make resource limits explicit. |

The local agent remains independently usable. These evolutions do not add a
mandatory central service, paid entitlement, AI provider or shared database.

## Historical compatibility and validation record

The maintainer selected the documented compatibility contract for **1.0.0** on
2026-09-28. Publication remains gated by same-revision regression, installed
package/image/UI checks and upgrade/rollback workflows. A version number is not
a certification of every environment or a promise of bug-free software.

The open field-validation items below remain open: selecting the 1.0 contract
does not convert pending extended-field checks into completed tests. SSH
acceptance now has separate post-release evidence, recorded below. See
[1.0.0 release notes](release-1.0.0.md) for the release
scope and known limits, and inspect the tagged revision's workflow results for
actual automated acceptance.

## 2.0.0 — delivered release scope

On 20 November 2026 the maintainer authorized publishing 2.0.0 with optional
AI incident assistance, opt-in SQLite accounts and the corrected web experience.
Real-model/API manual qualification and physical-device acceptance were deferred;
they were not prerequisites to the authorized release and remain separate gates.

Implemented: French/English AI instructions and preview consent, CLI language
option, persisted web language choice, catalog-based authentication/AI text,
and branded responsive sign-in. Legacy authentication remains the default.
The public demo must open directly without a login form; authenticated deployments
retain their sign-in screen and server-enforced permissions.

Publication requires the same-revision automated package, Docker, upgrade,
proxy, browser and documentation checks. Registry publication and site/demo
deployment are verified separately after those checks. See [release notes](release-2.0.0.md).

Still pending for the server: real-model quality and language review, physical
notification acceptance and full translation of the remaining dashboard views.
Application distribution and device acceptance are tracked separately. Shared-library
review found no reusable browser catalog facility in DWho/HTTPdis/Sonicprobe;
self-hosted i18next handles presentation, while prompt policy stays in the core.

## 1.3.3

The agent requires HTTPdis 0.7.1 or later.

## 1.3.2 HTTP transport dependency

The agent requires HTTPdis 0.6.39 or later.

## 1.3.1 filtered Docker socket by default

The repository quickstart builds the filtered proxy for new installations.
[Migration and per-target grants](compose-quickstart.md#migrate-an-existing-agent)
cover existing deployments. The v1.3.0 tag retains its original Compose files.

## 1.3.0 configuration for direct commands

The [1.3.0 release](release-1.3.0.md) adds per-command YAML settings and optional bearer authentication for status reads.

## 1.2.0 security and reliability

See the [upgrade notes](release-1.2.0.md) before updating API credentials or the journal proxy.

## 1.1.4 guided installation

The [1.1.4 release](release-1.1.4.md) includes the local Compose installer and repeat-installation checks.

## 1.1.3 maintenance update

The [1.1.3 release notes](release-1.1.3.md) cover historical snapshots in the dashboard and spaced membership lists in rules.

## Available in the 0.0.x series

- One-shot commands and continuous monitoring, sharing selectors and rules.
- Named YAML scenarios, configuration validation and dry runs.
- Resource, health, filesystem and access checks, including Linux ACLs.
- Action timing, restart limits, protected containers and temporary maintenance.
- Optional mobile-friendly UI, manual actions and correlated action history.
- Persistent audit journal, bounded reads and CSV/JSONL exports.
- Prometheus/Grafana integration and notification examples.
- Automated regression tests, Docker Hub/PyPI releases and a live Docker demo.

The 0.0.82 release includes the explicit read-only terminal interface; earlier
releases added action-history previews and bounded journal exports. Local function benchmarks are not measurements of whole-page latency.

## Validation record and continuing work

### 1. Define the compatibility contract — selected for 1.0

- [x] Inventory current YAML configuration, scenario and selector syntax.
- [x] Inventory CLI behavior and exit codes with regression coverage.
- [x] Resolve the five CLI/YAML compatibility decisions (0.0.80).
- [x] Select the documented 1.0 contract by maintainer decision (2026-09-28);
  same-revision automated acceptance remains mandatory before publication.
- [x] Document HTTP API fields and metric names as compatibility baselines with regression coverage.
- [x] Define journal schema evolution and compatibility with retained events.
- [x] State supported Python/Docker environments and optional check prerequisites.
- [x] Publish deprecation and migration rules for future incompatible changes.

Completion: each supported public interface has a documented contract and
regression coverage for its important behavior.

The [configuration and CLI baseline](config-cli-contract.md) records the first
review, including the corrected offline access-resource validation and remaining
legacy decisions. This inventory is the documented 1.0 interface baseline.

The [HTTP API baseline](http-api-contract.md) and [metrics catalogue](metrics.md)
now cover field types, authentication, errors, freshness, action results, journal
pages and metric names/types/units/labels. Targeted wire-level tests protect these
behaviors. The HTTP and five CLI/YAML decisions have now been approved; their 0.0.80
implementation and migration examples are tracked in [beta decisions](beta-decisions.md).
The open validation items below are recorded independently of that contract.

The [deprecation and migration policy](deprecation-policy.md) now defines advance
release notices, replacements, urgent exceptions and migration/rollback evidence.
It does not remove a feature or certify untested deployment environments.

### 2. Validate installation and upgrades — in progress

The [installation/upgrade rehearsal](installation-upgrades.md) now provides a
repeatable published 0.0.78/0.0.79/0.0.82-to-candidate matrix, current-data and backup
rollback reads, installed cron/serve checks and published UI acceptance. Inspect
the workflow evidence before declaring these milestones complete.

The [environment baseline](supported-environments.md) distinguishes CI-tested
Python/Linux combinations, Docker coverage and target-container prerequisites.
The optional [migration command](audit-migration.md) now validates retained
journals and creates verified schema 2 copies with original-byte backups.

The [journal compatibility baseline](journal-compatibility.md) defines supported
schemas, read-time adaptation, mixed-history regression coverage, export limits
and future conversion requirements. Its backup/rollback procedure still needs
the versioned deployment rehearsals below; the full upgrade milestone remains open.

- [x] Establish repeatable fresh-install and upgrade/rollback rehearsals for PyPI
  and Docker, preserving configuration, journal and action state.
- [x] Gate every publication on rehearsals for the exact candidate, including
  0.0.82; the tagged workflow run records whether execution actually succeeded.
- [x] Document backup and rollback steps, including schema restrictions.
- [x] Include installed cron/serve and optional UI checks in the release gates;
  preserve the run-specific results with the upgrade artifacts.

Completion: reproducible installation/upgrade procedures and their results are
recorded, including the versions actually tested.

### 3. Establish performance and resilience baselines — in progress

The [performance and resilience baseline](performance-resilience.md) adds measured
1/50 MiB workloads, loopback HTTP timing, resource counters and failure-injection
checks. A finite two/six-reader HTTP load harness also records separate successful
and busy latencies, durable writer progress, RSS/descriptor samples and post-load
recovery during rotation. Longer soak tests remain pending.
Whole-deployment/browser latency and physical storage failure rehearsals
remain outside this first baseline.

- [x] Record local 1/50 MiB journal, filter, action-history, rotation and export
  benchmarks with dataset and environment details.
- [ ] Repeat measurements on the fixed deployment candidate.
- [ ] Measure CPU, memory, disk I/O and end-to-end latency; distinguish server
  processing from network and browser time.
- [x] Test Docker unavailability, duplicate actions, process crashes and injected
  storage failures, with recovery and limitations documented.
- [ ] Observe a representative deployment through failures and prolonged load.
- [ ] Define acceptable resource and latency bounds for reference workloads.

Completion: reproducible measurements meet the documented bounds, and failures
remain explicit without unbounded resource consumption or misleading outcomes.

### 4. Stabilize the read-only terminal interface — delivered in 0.0.82

See the [terminal interface scope and limits](terminal.md).

- [x] Provide `monit-docker tui` for container status, resource/check measurements
  and bounded recent journal inspection, using DWho presentation components.
- [x] Preserve non-interactive CLI/cron output, exit codes and startup: no automatic
  curses activation, including when a terminal is detected.
- [x] Keep terminal rendering separate from shared monitoring services; execute
  no rules or manual actions from this first interface.
- [x] Automate Docker/PTY navigation, resizing, clean exit and read-only checks
  for the candidate and published 0.0.82 (PR #76).
- [x] Complete real SSH acceptance of published 1.0.0 on Debian 13 (2026-09-28):
  navigation, resizing, journal, quit/Ctrl-C, terminal restoration and piped refusal.
- [ ] Complete prolonged candidate observation and review the final measurements.

Interactive mutations are deferred. The first release is read-only and must be
included in continued field validation. The SSH acceptance scope and remaining
transport limits are recorded in the terminal guide; automated PTYs alone do not
establish those results.

### 5. Complete extended field validation — open

The pre-release hardening candidate adds discovered unittest architecture guards,
normalized selector errors, bounded statistics bodies, signal cleanup and a
release workflow requiring regression and upgrade jobs for the same revision.
These changes have automated CI coverage; candidate observation remains a
separate open activity.
The intermittent OOM integration failure is not yet explained; retain the separate
instrumented evidence rather than treating a successful rerun as a fix.


- [ ] Use a fixed candidate in real deployments for a documented observation period.
- [ ] Triage reported issues and resolve every release-blocking regression.
- [ ] Review permissions, protected containers, maintenance and action attribution.
- [ ] Recheck desktop/mobile behavior and publish release notes and known limits.

Completion: the outstanding field checks have recorded evidence and any
confirmed regression is triaged. This section is not marked complete by the
1.0 compatibility decision or a green automated release workflow.

## Later candidates, not 1.0 commitments

- Additional checks and reusable scenario examples driven by real use cases.
- Journal indexing or an alternative storage backend only if measured workloads
  justify the added complexity.
- Broader action-history navigation and export options based on user feedback.

### Scenario Group (planned)

Add named, ordered groups of existing scenarios as a later product enhancement.
This is not part of the delivered 1.0 scope.

- Proposed CLI: `monit-docker run --scenario-group maintenance`; preserve
  `monit-docker run NAME`, existing container selectors and `ctn-groups`.
- Keep scenario groups distinct from container groups. Each scenario retains
  its own container selection and existing state/audit policies.
- Validate all references offline before connecting to Docker. Initially use
  flat ordered groups, rejecting duplicate members and nested group references.
- Start with finite `stats` and `cron` scenarios. Reject continuous `serve`
  members until a separate lifecycle design exists.
- Run members sequentially; stop the group on the first failure or unknown
  outcome by default and mark later members skipped. Do not imply rollback.
- Report each scenario's result and preserve existing exit-code semantics;
  specify and test the aggregate exit-code contract before implementation.
- Implement orchestration in an interface-independent application service,
  reusing existing execution protections rather than calling CLI entry points.

Use the same `scenario-group` term as Auton, without coupling their engines or
changing their different execution models. This capability is not implemented.

### Single-level configuration imports (planned hardening)

Existing section imports already help separate scenarios, container groups,
conditions and commands. Keep their documented `@import_*` syntax; do not add
a competing generic `!include` mechanism or impose Auton's client schema.

- Audit the current loader, template evaluation order and import behavior before
  changing it. Define one import level: the main configuration may import section
  files; imported files must not import further files. Reject nesting explicitly.
- Document path resolution from the main configuration directory, the expected
  section-file shape, and existing import/inline precedence.
- Bound file count and aggregate input size, diagnose missing/repeated files and
  invalid structures, and validate references before Docker access or actions.
- Preserve the documented 1.0 import and override contract. If stronger rejection
  changes an accepted configuration, provide an explicit compatibility decision,
  migration example and regression tests rather than silently changing behavior.
- Test the import boundaries offline through `check-config`, including attempted
  nested/self imports, malformed files, relative paths and reference errors.
- Apply the same one-level policy to future scenario-group definitions when that
  feature arrives. Shared principles do not require a shared runtime framework.

This item records planned verification/hardening, not completed enforcement.

### Basic open-source AI assistance — delivered in 2.0.0

Scope approved on 18 November 2026. The 20 November candidate implements a
standalone `explain` command, with explicit preview, bounded evidence and optional
Ollama, OpenAI-compatible or native Anthropic/Claude configuration. An opt-in
eight-scenario synthetic evaluation harness records latency and available usage
for human review. The opt-in HTTP/UI integration now includes explicit preview
and consent, dedicated AI authorization, bounded admission and escaped results.
Real-model quality remains unqualified; optional assistance was published in 2.0.0.

- Add optional assistance for a standalone Monit-Docker installation, using the
  user's own AI API key. API usage is billed directly by the chosen provider.
  Allow a compatible local model where supported.
- Start with a small read-only scope: explain an incident, summarize relevant
  recent events, and suggest checks based on available measurements and rules.
- Trigger analysis explicitly. Distinguish observed facts from hypotheses and
  identify missing evidence; do not apply changes or run commands automatically.
- Bound the context sent to the model, exclude credentials, and make transmitted
  data and provider configuration clear to the operator. Keep API keys out of
  logs and generated explanations.
- Preserve normal monitoring, local YAML configuration and existing protections
  when AI is disabled, unavailable or misconfigured.

The [user guide](incident-ai.md) documents the delivered protocol and limits.
The [contributor review](incident-ai-review.md) retains historical automated
coverage and remaining real-model qualification; its older release-pending text
is superseded by the published 2.0 release record. Automated schema checks do
not prove model usefulness.

## Design constraints

Keep the DWho, HTTPdis and Sonicprobe foundations, global constants and a light
mobile interface. Keep the simple mode useful without a monitoring stack. Authentication and AI labels already support French/English; translating the
remaining dashboard labels is the immediate priority. Machine-readable codes
and backend diagnostic text remain unchanged. Prefer measured
improvements and documented behavior over adding infrastructure by default.

Discuss priorities through [GitHub issues](https://github.com/decryptus/monit-docker/issues).
The maintainer may revise the order and scope as evidence and user needs change.

## SQLite authentication — released in 2.0.0

Released in 2.0.0: local accounts and global roles, durable sessions, scoped machine tokens, browser login/logout, native session API, offline htpasswd migration with explicit role mapping and non-destructive rollback. See the [engineering record](authentication-review.md) and [operator guide](authentication.md).

Automated checks cover the supported Python suites, installed packages, real TLS proxy migration and browser authentication (see [PR #119](https://github.com/decryptus/monit-docker/pull/119) for revision-specific results). The matching native session client exists in the mobile application. The 2.0.0 package/images and public demo have been delivered; anonymous demo status is read-only while private routes still require authentication. Physical mobile acceptance remains separate. Do not infer device acceptance from CI or server checks.

## Account lifecycle and optional device delivery — delivered after 2.0.0

- Implemented: own display name/language, current-password change, offline-admin
  one-time recovery codes, credential revocation and bounded device registration.
- Implemented: optional independent Expo delivery worker, episode deduplication,
  persisted retry backoff, receipt checks and generic EN/FR payloads.
- Existing sessions: secure native protocol, 8-hour absolute / 30-minute idle
  lifetime, explicit reauthentication; no refresh credential or QR pairing.
- Validation: full Python 3.10/3.12, package/image, Docker integration, proxy/browser
  and documentation checks passed. Account lifecycle was also verified on the demo
  with a disposable account, including recovery replay and session revocation.
- Delivery: these endpoints and the optional worker are included in the published
  2.0.2 packages/images and deployed demo. The original 2.0.0 artifacts remain
  immutable. Each installation still needs its own push configuration and device
  opt-in; physical-phone receipt remains a separate gate. See
  [mobile accounts](mobile-accounts.md).

## Email account identifiers — delivered in 2.0.2

New CLI/API accounts require email syntax; legacy login/reset/import stay
compatible. Server startup reports the count of accounts without email
identifiers once. HTTPdis owns durable identity/session storage; Sonicprobe's
existing mailbox validator is reused with Monit-Docker's bounded ASCII account
policy. The web client accepts these identifiers; third-party clients must also
support the longer format. Mailbox verification and automatic email recovery
remain planned evolutions, not capabilities implied by the 2.0.2 release.
