# Docker healthchecks

The `health` resource reads Docker's existing healthcheck result. Docker executes
the healthcheck according to the image or Compose configuration; monit-docker
does not install or execute another probe. Health-only rules need no resource
statistics stream or container exec.

| Value | Meaning |
| --- | --- |
| `healthy` | Docker reports a passing healthcheck on a running container |
| `unhealthy` | Docker reports a failing healthcheck on a running container |
| `starting` | Docker is still establishing the initial health result |
| `none` | No healthcheck is configured, or it is explicitly disabled |
| `unknown` | A configured check has no recognized result, or the container is not running |

An old `healthy` result on a paused or stopped container is exposed as `unknown`.
A container without a healthcheck is never assumed healthy. Only normalized
states are exposed: Docker's healthcheck output/logs are not copied into the API
or metrics. Values refresh at each monitoring cycle, not at every HTTP scrape.

## Read or act on health

```sh
monit-docker --name 'web-*' stats --rsc health
monit-docker --name 'web-*' monit --dry-run \
  --cmd-if 'health == unhealthy ? restart'
```

Remove `--dry-run` only when the selected restart action is intended. With `cron`
or `serve`, use the existing cooldown and optional trigger delay to control
repeated attempts. For example:

```sh
monit-docker --name 'web-*' cron \
  --state-file /var/lib/monit-docker/health.json \
  --cooldown 300 --trigger-after 120 --max-gap 90 \
  --cmd-if 'health == unhealthy ? restart'
```

Schedule that example once per minute. It requires an observed unhealthy state
for at least 120 seconds, with no observation gap over 90 seconds. `starting`,
`healthy`, `none` and `unknown` do not match and reset the observed unhealthy
streak. Docker's own healthcheck retry/start-period behavior still applies.

Health conditions support `==`, `!=`, `in` and `not in`, with validated state
names. Prefer `health == unhealthy` for remediation: `health != healthy` also
matches containers without a check or still starting. YAML aliases work as usual:

```yaml
conditions:
  unhealthy:
    expr:
      - health == unhealthy
```

Use `--cmd-if '@unhealthy ? restart'` with the configuration file. Health-only
rules run in the engine's existing state-rule phase, before resource sampling.

For command-line supervision, `monit --rsc health` returns a process exit code
for the first selected container, as `monit --rsc status` does:

| Health | Exit code |
| --- | --- |
| `healthy` | 0 |
| `starting` | 10 |
| `unhealthy` | 20 |
| `none` | 30 |
| `unknown` | 115 |

Use `stats --rsc health` to read every selected container rather than just one
exit code. Connection/configuration errors retain their existing exit codes.

## API, UI and Prometheus

`/v1/status` includes `health` in each container snapshot. The UI displays it
under the container status, including absence of a check. In `serve` mode,
`monit_docker_container_health_status{id="...",name="...",health="unhealthy"} 1`
describes the current health state. Only the current state is emitted. No
container samples are emitted when the service cache is stale or collection
fails; consult `monit_docker_ready` and Prometheus `up` as well.

An optional Prometheus alert can use the existing Alertmanager notification
pipeline:

```yaml
- alert: MonitDockerContainerUnhealthy
  expr: |
    (monit_docker_container_health_status{job="monit-docker",health="unhealthy"} == 1)
    and on (job, instance) (monit_docker_ready{job="monit-docker"} == 1)
    and on (job, instance) (up{job="monit-docker"} == 1)
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: 'Container {{ $labels.name }} is unhealthy'
```

This rule alerts only on `unhealthy`; use separate rules if missing healthchecks
or `unknown` states should also be reported in your environment.
