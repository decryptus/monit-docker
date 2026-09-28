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

### Predictive monitoring and optional AI assistance — exploratory TODO

Idea recorded on 2026-09-28. This is a post-1.0 candidate, not a delivery
commitment or a change to current stabilization priorities. The primary goal is
to anticipate incidents from metric history; explanations support that goal.

- [ ] Prototype read-only forecasting of disk-space or inode exhaustion, with
  an explicit forecast horizon and conditions under which estimates are valid.
- [ ] Obtain sufficiently long, timestamped metric history through an adapter.
  Evaluate existing sources such as Prometheus without making them mandatory
  for the local agent. Current values and action logs alone are insufficient.
- [ ] Compare simple trend extrapolation with more complex time-series models.
  Handle missing data, restarts, deployments, capacity changes and seasonality;
  abstain when the available evidence does not support a useful forecast.
- [ ] Report estimates, uncertainty, data provenance and model version. Keep
  current anomalies, predicted threshold crossings and suspected causes distinct.
- [ ] Evaluate on held-out future periods: false alerts, missed incidents,
  useful warning time and operator time saved, compared with fixed thresholds
  and simple forecasting baselines. Start in observation mode, then alert only.
- [ ] As a complementary feature, explain incidents and forecasts with cited
  events, timestamps and relevant rules. Distinguish observations, hypotheses
  and suggested checks; identify incomplete history explicitly.
- [ ] If useful, explore incident summaries and proposed YAML diffs validated
  with `check-config`. Applying changes remains an explicit operator decision.

Time-series statistics or ML produce forecasts; a language model may explain
them but is not the source of truth for predicted values. Keep AI optional and
disabled by default, with local/remote model adapters, explicit data-sharing
choices, secret redaction and bounded requests. Treat journal/configuration text
as evidence, never as instructions granting the model authority. Monitoring and
remediation must retain deterministic behavior without a model or on failure.

Long-term direction: Docker is the first use case. If Covenant becomes the
shared supervision/automation foundation, reuse validated prediction/evidence
contracts across sources, with Docker and later Kubernetes details in adapters.
Validate predictions in each environment and respect Kubernetes workload
controllers rather than copying container-level actions blindly. Extract shared
machinery only after validating real needs; Covenant, Kubernetes and Centrex
remain optional for the local agent. This is an architectural intention, not an
implemented platform.

Autonomous actions are outside the prototype. Any later action assistance needs
a separate decision, explicit human approval and the existing authorization,
protected-resource, maintenance and action-budget checks.

## Design constraints

Keep the DWho, HTTPdis and Sonicprobe foundations, global constants and a light
mobile interface. Keep the simple mode useful without a monitoring stack. Code,
UI labels and machine-readable reasons remain in English. Prefer measured
improvements and documented behavior over adding infrastructure by default.

Discuss priorities through [GitHub issues](https://github.com/decryptus/monit-docker/issues).
The maintainer may revise the order and scope as evidence and user needs change.
