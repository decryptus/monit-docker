# monit-docker 1.0.1

This maintenance release fixes Docker statistics timeout reporting and reuses
Sonicprobe XYS for structural YAML validation. The local CLI/cron, explicit TUI,
HTTP `/v1`, metrics and journal contracts remain unchanged.

## Changes since 1.0.0

- Convert typed socket, connection/header and wrapped read timeouts into
  `MonitoringError(115)`, even when the socket fails before the body watchdog.
  Unrelated transport errors retain their original behavior. Timer cancellation,
  joining and response cleanup remain in place.
- Share root, mapping and string-list shape validation with Sonicprobe XYS.
  Existing selector, permission and scenario semantics remain in force; this
  does not introduce implicit comma splitting or different precedence.
- Require Sonicprobe 0.3.57 or later. The timeout race also occurred with 0.3.56;
  upgrading Sonicprobe alone does not fix it.

## Installation and upgrade

```sh
python -m pip install --upgrade 'monit-docker==1.0.1'
```

Pin `decryptus/monit-docker:1.0.1` and, when used,
`decryptus/monit-docker-ui:1.0.1`. Images target Linux amd64; the `latest` tag is
not updated. No configuration or journal migration is required from 1.0.0.
Preserve configuration, state, restart budgets, journals and retained archives;
follow the [backup and rollback procedure](installation-upgrades.md).
For older beta installations, read the [1.0.0 release notes](release-1.0.0.md).

## Validation and limits

The release workflow gates publication on unittest collection, source and
installed-package regression suites, Docker and UI checks, documentation, and
upgrade/rollback rehearsals for the same revision. Deterministic timeout tests
cover socket-first and watchdog-first ordering, connection/header failures,
unrelated errors and failing cleanup. The application suite now contains 407
cases; opt-in integration cases require their dedicated jobs.

Workflow results are the validation evidence. The statistics body deadline is
not an absolute deadline for the entire monitoring cycle or every action.
This patch does not establish absence of memory leaks, behavior under saturation
or new prolonged field-observation results. The existing
[compatibility limits](release-1.0.0.md#validation-status-and-remaining-limits)
remain applicable. Publication and deployment are separate operations.
