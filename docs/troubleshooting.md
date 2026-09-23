# Troubleshooting

For configuration errors, start with [`check-config`](check-config.md), which works
without a Docker connection. Then inspect the command output and logs. In `serve` mode, also inspect
`http://127.0.0.1:9808/v1/status`: `last_error_code` describes the last failed
cycle. A reachable HTTP endpoint alone does not mean Docker monitoring works.

| Symptom | What to check |
| --- | --- |
| `serve` is not recognized | Run `python -m pip show monit-docker` in the environment used by the command. Install version 0.0.56 or newer. |
| Docker connection or permission error (170/180) | Check that the daemon is running and accessible to the same account. Check the selected client configuration or `DOCKER_HOST`; use `--client-from-env` before the subcommand to explicitly select environment settings. |
| No matching container (114) | Check the name/ID and filters. Replace example names with your own; quote wildcards such as `'web*'`. `serve` remains unready when nothing matches. |
| `/healthz` is 200 but `/readyz` is 503 | Wait for the first successful cycle, then check `/v1/status` and logs. A failed cycle or expired cache makes readiness false. |
| `curl` cannot connect | Leave `serve` running in another terminal. Check the bind address, port and startup logs. The default is `127.0.0.1:9808`. |
| Prometheus cannot scrape | Test connectivity from Prometheus's network namespace. Its `127.0.0.1` is not the host or another container. Check the target address, agent bind address and published port/private network. |
| Grafana shows **No data** | Select the correct Prometheus data source, Job, Instance and Container. Check `up` and `monit_docker_ready` in Prometheus. Rate panels need multiple scrape samples; stopped containers have no sampled resource values. |
| A rule produces no action | Check the selection and condition. Nonmatching rules produce no decision. `--dry-run` simulates actions, an active cooldown skips them, and a [trigger delay](trigger-delay.md) reports `pending` until its duration elapses. |
| Job is busy (117) | Another cycle holds the lock for that state path. Let it finish or investigate the running process; do not delete the lock file. |
| State error (118) | Check directory permissions, available storage and state validity. See the [state recovery procedure](cron.md#locking-and-persistent-state) before changing state. |

Global selectors and configuration options must precede `stats`, `monit`, `cron`
or `serve`. Run `monit-docker --help` and `monit-docker serve --help` to check
option placement.

For the expected behavior, see [simple mode](simple.md), [serve mode](serve.md),
the [metrics reference](metrics.md) and [Grafana setup](grafana.md).
