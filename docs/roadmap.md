# Roadmap to 1.0

monit-docker remains in the **0.0.x** series while its public interfaces settle.
Version 1.0 is a compatibility commitment, not a feature-count target or a promise
of bug-free software. No release date is committed. This roadmap is maintained on
the default branch; its open milestones are planned work, not completed checks.

## Available in the 0.0.x series

- One-shot commands and continuous monitoring, sharing selectors and rules.
- Named YAML scenarios, configuration validation and dry runs.
- Resource, health, filesystem and access checks, including Linux ACLs.
- Action timing, restart limits, protected containers and temporary maintenance.
- Optional mobile-friendly UI, manual actions and correlated action history.
- Persistent audit journal, bounded reads and CSV/JSONL exports.
- Prometheus/Grafana integration and notification examples.
- Automated regression tests, Docker Hub/PyPI releases and a live Docker demo.

The 0.0.76 release adds immediate action-history previews and faster audit text
validation. Local function benchmarks are not measurements of whole-page latency.

## Before 1.0

### 1. Define the compatibility contract — in progress

- [x] Inventory current YAML configuration, scenario and selector syntax.
- [x] Inventory CLI behavior and exit codes with regression coverage.
- [ ] Resolve the open compatibility decisions and approve the 1.0 contract.
- [x] Document HTTP API fields and metric names as compatibility baselines with regression coverage.
- [x] Define journal schema evolution and compatibility with retained events.
- [x] State supported Python/Docker environments and optional check prerequisites.
- [x] Publish deprecation and migration rules for future incompatible changes.

Completion: each supported public interface has a documented contract and
regression coverage for its important behavior.

The [configuration and CLI baseline](config-cli-contract.md) records the first
review, including the corrected offline access-resource validation and remaining
legacy decisions. This inventory does not yet freeze a 1.0 interface.

The [HTTP API baseline](http-api-contract.md) and [metrics catalogue](metrics.md)
now cover field types, authentication, errors, freshness, action results, journal
pages and metric names/types/units/labels. Targeted wire-level tests protect these
behaviors. The HTTP and five CLI/YAML decisions have now been approved; their 0.0.80
implementation and migration examples are tracked in [beta decisions](beta-decisions.md).
Final 1.0 approval still requires the remaining milestones.

The [deprecation and migration policy](deprecation-policy.md) now defines advance
release notices, replacements, urgent exceptions and migration/rollback evidence.
It does not remove a feature or approve the remaining 1.0 decisions.

### 2. Validate installation and upgrades — in progress

The [installation/upgrade rehearsal](installation-upgrades.md) now provides a
repeatable published 0.0.78/0.0.79-to-candidate matrix, current-data and backup
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

- [ ] Rehearse fresh installations from both PyPI and the published Docker images.
- [ ] Exercise upgrades from documented supported 0.0.x versions while preserving
  configuration, retained journal entries and relevant persistent action state.
- [ ] Document backup and rollback steps, including any schema restrictions.
- [ ] Verify cron and serve modes with and without the optional UI.

Completion: reproducible installation/upgrade procedures and their results are
recorded, including the versions actually tested.

### 3. Establish performance and resilience baselines — in progress

The [performance and resilience baseline](performance-resilience.md) adds measured
1/50 MiB workloads, loopback HTTP timing, resource counters and failure-injection
checks. Whole-deployment/browser latency and physical storage failure rehearsals
remain outside this first baseline.

- [ ] Benchmark ordinary and large retained journals, filters, action history,
  rotation and exports with explicit datasets and hardware details.
- [ ] Measure CPU, memory, disk I/O and end-to-end latency; distinguish server
  processing from network and browser time.
- [ ] Exercise Docker unavailability, concurrent actions, process restarts and
  storage failures; document observed limits and recovery behavior.
- [ ] Define acceptable resource and latency bounds for reference workloads.

Completion: reproducible measurements meet the documented bounds, and failures
remain explicit without unbounded resource consumption or misleading outcomes.

### 4. Complete a stabilization period — planned

- [ ] Use a fixed candidate in real deployments for a documented observation period.
- [ ] Triage reported issues and resolve every release-blocking regression.
- [ ] Review permissions, protected containers, maintenance and action attribution.
- [ ] Recheck desktop/mobile behavior and publish release notes and known limits.

Completion: all earlier milestones have evidence, no release-blocking issue
remains, and the maintainer explicitly approves the 1.0 compatibility commitment.
Until then, releases stay in the 0.0.x series.

## Later candidates, not 1.0 commitments

- Additional checks and reusable scenario examples driven by real use cases.
- Journal indexing or an alternative storage backend only if measured workloads
  justify the added complexity.
- Broader action-history navigation and export options based on user feedback.

### Optional AI incident explanations — exploratory TODO

Idea recorded on 2026-09-28. This is a post-1.0 candidate, not a delivery
commitment or a change to the current stabilization priorities.

- [ ] Prototype a read-only **Explain** entry point on an incident. A possible
  CLI counterpart is `monit-docker explain <container>`; syntax is not yet decided.
- [ ] Assemble a bounded evidence snapshot from available measurements, relevant
  YAML rules, correlated audit events, action results and restart protections.
  Identify missing, expired or incomplete history explicitly.
- [ ] Cite event IDs/timestamps and relevant rules in explanations. Separate
  observed facts, possible causes and suggested checks; report insufficient
  evidence instead of inventing a diagnosis.
- [ ] Evaluate usefulness on representative incidents, including ambiguous cases:
  supported claims, incorrect diagnoses, time saved, latency and cost. Expand
  only if the prototype provides measurable value.
- [ ] If useful, explore incident-period summaries and proposed YAML changes
  presented as reviewable diffs and validated with `check-config`. Applying a
  change remains an explicit operator decision.

Keep AI optional and disabled by default. Monitoring and remediation must retain
their deterministic behavior without a model or when inference fails. Explore
local and remote model adapters with explicit control over transmitted data,
secret redaction and bounded requests. Treat journal/configuration text as
evidence, never as instructions granting the model authority.

Long-term direction: monit-docker is the first incident-explanation use case.
If Covenant becomes the shared supervision/automation foundation, make the
evidence and explanation contracts reusable across event sources, with Docker
details in adapters. Keep model integration separate from deterministic policy
and execution. Validate the local prototype before extracting shared machinery;
neither Covenant nor Centrex becomes mandatory for the local agent. This is an
architectural intention, not a claim that the shared platform already exists.

Autonomous actions are outside the prototype. Any later action assistance needs
a separate decision, explicit human approval and the existing authorization,
protected-container, maintenance and restart-budget checks.

## Design constraints

Keep the DWho, HTTPdis and Sonicprobe foundations, global constants and a light
mobile interface. Keep the simple mode useful without a monitoring stack. Code,
UI labels and machine-readable reasons remain in English. Prefer measured
improvements and documented behavior over adding infrastructure by default.

Discuss priorities through [GitHub issues](https://github.com/decryptus/monit-docker/issues).
The maintainer may revise the order and scope as evidence and user needs change.
