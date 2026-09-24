# Prometheus metrics reference

`monit-docker serve` exposes `/metrics` using the
[Prometheus text exposition format 0.0.4](https://prometheus.io/docs/instrumenting/exposition_formats/).
The response is UTF-8 with content type
`text/plain; version=0.0.4; charset=utf-8`. Scrapes read the memory cache and never
start a monitoring cycle. See [serve configuration and freshness](serve.md).

## Agent metrics

| Metric | Type | Unit / values | Labels | Meaning |
| --- | --- | --- | --- | --- |
| `monit_docker_ready` | gauge | 0 or 1 | none | 1 when the last completed cycle succeeded and is fresh |
| `monit_docker_cycle_running` | gauge | 0 or 1 | none | 1 while a cycle is collecting or applying rules |
| `monit_docker_cycles_total` | counter | cycles | none | Completed cycles since process startup, including failures |
| `monit_docker_cycle_errors_total` | counter | cycles | none | Failed cycles since startup, including lock or state errors |
| `monit_docker_last_success_timestamp_seconds` | gauge | Unix seconds | none | Completion time of last successful cycle; absent until first success |
| `monit_docker_action_decisions_total` | counter | actions | `outcome` | Successful executions or skipped/simulated actions since startup |

`outcome` is exactly one of `executed`, `cooldown`, `pending`, `restart-limit`, or `dry-run`. A rule with
multiple actions can increment multiple decisions. Failed actions do not count
as `executed`; the failed cycle increments `cycle_errors_total`. Successful
actions earlier in a failed cycle remain counted. A rule that does not match
produces no decision. All four outcome series are present at startup, at zero.

`pending` counts actions skipped because their local [trigger delay](trigger-delay.md)
has not elapsed. It is not a gauge of currently pending rules.

Agent counters reset when the process restarts. They are not saved to the
cooldown file. Last-success time remains visible after failure/staleness; it does
not by itself indicate readiness. Prometheus's own `up` series measures whether
scraping works; use `monit_docker_ready` to check whether monitoring works.

## Container metrics

All container samples have `id` (full Docker container ID) and `name` labels.
`container_info` additionally has the Docker `status` label. Replacing a container
creates a new ID/series; renaming changes the name label. Names and IDs can
therefore create historical series churn. No command, arbitrary container label
or exception message is used as a metric label.

| Metric | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `monit_docker_container_info` | gauge | always 1 | Identity and status from the cached snapshot |
| `monit_docker_container_health_status` | gauge | always 1 | Current normalized health state in the additional `health` label |
| `monit_docker_container_restart_attempts` | gauge | attempts | Automatic restart reservations since explicit rearm; present with automatic rules |
| `monit_docker_container_restart_limit` | gauge | attempts | Configured automatic restart limit; present with automatic rules |
| `monit_docker_container_restart_blocked` | gauge | boolean | Budget exhausted (1) or available (0); present with automatic rules |
| `monit_docker_container_memory_usage_bytes` | gauge | bytes | Docker memory usage, minus `total_cache` when that field exists |
| `monit_docker_container_memory_limit_bytes` | gauge | bytes | Limit reported by Docker |
| `monit_docker_container_memory_usage_percent` | gauge | percent | Memory usage / reported limit × 100 |
| `monit_docker_container_cpu_usage_percent` | gauge | percent | CPU usage computed from two Docker samples; may exceed 100 on multiple cores |
| `monit_docker_container_io_read_bytes_total` | counter | bytes | Cumulative block-I/O reads summed from Docker's Read entries |
| `monit_docker_container_io_write_bytes_total` | counter | bytes | Cumulative block-I/O writes summed from Docker's Write entries |
| `monit_docker_container_network_receive_bytes_total` | counter | bytes | Cumulative received bytes summed over Docker interfaces |
| `monit_docker_container_network_transmit_bytes_total` | counter | bytes | Cumulative transmitted bytes summed over Docker interfaces |

The `health` label is `healthy`, `unhealthy`, `starting`, `none` (no active check),
or `unknown` (no usable current result). Only the current state is emitted;
healthcheck command output is excluded. See [Docker healthchecks](healthchecks.md).

The byte totals are Docker counters, not deltas per scrape. They can reset on a
container/daemon restart; use Prometheus `rate()` to compute bytes per second.
CPU and memory percentages retain the existing CLI calculation semantics. For
example, CPU value 230 means 230%, not a 0–1 ratio. Missing network/block-I/O
sections currently produce zero totals, consistent with the existing collector.

Unavailable and unrequested measurements are omitted, never converted from null
to zero. Stopped containers still have `container_info` but no sampled resource
values. At startup, after a failed cycle, or once the cache is stale, all container
samples are omitted; agent metrics remain present. No explicit sample timestamps
are sent: Prometheus timestamps each scrape. Scraping faster than the monitoring
interval can therefore record the same cached values more than once.

## Filesystem metrics

Filesystem samples add `group` and `path` labels to the container `id` and
`name`. Collection is opt-in through resources or rules referencing a configured
directory group. See [disk space and inode checks](filesystems.md) for YAML,
selection, requirements and error handling.

| Metric | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `monit_docker_container_disk_usage_bytes` | gauge | bytes | Used filesystem space |
| `monit_docker_container_disk_available_bytes` | gauge | bytes | Space available to an unprivileged writer |
| `monit_docker_container_disk_total_bytes` | gauge | bytes | Total filesystem space, including reserved blocks |
| `monit_docker_container_disk_usage_percent` | gauge | percent | Used / (used + available) × 100 |
| `monit_docker_container_inode_usage` | gauge | inodes | Used inodes |
| `monit_docker_container_inode_available` | gauge | inodes | Free inodes |
| `monit_docker_container_inode_total` | gauge | inodes | Total inodes |
| `monit_docker_container_inode_usage_percent` | gauge | percent | Used / total inodes × 100 |

These are capacity gauges, not cumulative I/O counters: do not use `rate()` to
measure their occupancy. Paths report their containing filesystem's capacity,
not directory sizes. Paths sharing a filesystem can report identical values;
do not sum them as independent storage capacity. All available fields above
are exposed for each requested group. Missing inode accounting is omitted,
not reported as zero; explicitly requesting an unavailable field fails collection.
As with other container metrics, a failed or stale cycle withholds all samples.

## Prometheus configuration

For Prometheus running on the same host/network namespace as the default listener:

```yaml
scrape_configs:
  - job_name: monit-docker
    scrape_interval: 30s
    static_configs:
      - targets: ['127.0.0.1:9808']
```

If Prometheus runs in a separate container, `127.0.0.1` refers to that container.
Use a reachable private address or proxy for the agent and adjust `--bind` and
the target accordingly. The scrape interval does not change `serve --interval`.

Example queries:

```promql
# Monitor reachable but not producing fresh successful cycles
monit_docker_ready == 0

# Container memory usage in MiB
monit_docker_container_memory_usage_bytes / 1024 / 1024

# Network receive throughput in bytes per second
rate(monit_docker_container_network_receive_bytes_total[5m])

# Monitoring errors during the last hour
increase(monit_docker_cycle_errors_total[1h])
```

Also alert on `up{job="monit-docker"} == 0` for an unreachable/stopped process;
an unavailable target cannot emit its own readiness value.
The [alert guide](alerts.md) provides tested rules for availability, collection
readiness and memory usage, including how missing data affects each alert.

An [example Grafana dashboard](https://github.com/decryptus/monit-docker/blob/master/examples/grafana/monit-docker.json) is included;
see [import instructions and panel descriptions](grafana.md).
