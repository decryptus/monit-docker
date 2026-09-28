# Roadmap and 1.0 validation record

The maintainer selected the documented compatibility contract for **1.0.0** on
2026-09-28. Publication remains gated by same-revision regression, installed
package/image/UI checks and upgrade/rollback workflows. A version number is not
a certification of every environment or a promise of bug-free software.

The open field-validation items below remain open: selecting the 1.0 contract
does not convert missing SSH, prolonged-observation or OOM-diagnostic evidence
into completed tests. See [1.0.0 release notes](release-1.0.0.md) for the release
scope and known limits, and inspect the tagged revision's workflow results for
actual automated acceptance.

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
- [ ] Complete real SSH acceptance and prolonged candidate observation.

Interactive mutations are deferred. The first release is read-only and must be
included in continued field validation; automated PTY checks do not certify SSH.

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

## Design constraints

Keep the DWho, HTTPdis and Sonicprobe foundations, global constants and a light
mobile interface. Keep the simple mode useful without a monitoring stack. Code,
UI labels and machine-readable reasons remain in English. Prefer measured
improvements and documented behavior over adding infrastructure by default.

Discuss priorities through [GitHub issues](https://github.com/decryptus/monit-docker/issues).
The maintainer may revise the order and scope as evidence and user needs change.
