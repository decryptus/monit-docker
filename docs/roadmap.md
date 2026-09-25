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
- [ ] Publish deprecation and migration rules for future incompatible changes.

Completion: each supported public interface has a documented contract and
regression coverage for its important behavior.

The [configuration and CLI baseline](config-cli-contract.md) records the first
review, including the corrected offline access-resource validation and remaining
legacy decisions. This inventory does not yet freeze a 1.0 interface.

The [HTTP API baseline](http-api-contract.md) and [metrics catalogue](metrics.md)
now cover field types, authentication, errors, freshness, action results, journal
pages and metric names/types/units/labels. Targeted wire-level tests protect these
behaviors. Content-type and method-status differences remain explicit decisions
to resolve before approving the 1.0 contract.

### 2. Validate installation and upgrades — planned

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

### 3. Establish performance and resilience baselines — planned

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

## Design constraints

Keep the DWho, HTTPdis and Sonicprobe foundations, global constants and a light
mobile interface. Keep the simple mode useful without a monitoring stack. Code,
UI labels and machine-readable reasons remain in English. Prefer measured
improvements and documented behavior over adding infrastructure by default.

Discuss priorities through [GitHub issues](https://github.com/decryptus/monit-docker/issues).
The maintainer may revise the order and scope as evidence and user needs change.
