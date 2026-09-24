# Prometheus alerts for serve mode

The [Compose stack](compose.md) loads three alert rules from
`examples/monitoring/alerts.yml`. They run in Prometheus and require **serve
mode**. Simple commands and cron need no Prometheus installation and keep their
existing exit-code behavior.

For autonomous container actions, cron and serve also offer an optional
[local trigger delay](trigger-delay.md). It is configured separately and does not
alter these Prometheus alert durations.

## Defaults

| Alert | Condition | Continuous duration | Severity |
| --- | --- | --- | --- |
| `MonitDockerAgentDown` | Prometheus cannot scrape a configured agent (`up == 0`) | 2 minutes | critical |
| `MonitDockerCollectionNotReady` | Scrape succeeds, but readiness is 0 or absent | 3 minutes | warning |
| `MonitDockerContainerMemoryHigh` | Container memory usage is strictly above 90%, with the agent reachable and ready | 5 minutes | warning |

Rules select `job="monit-docker"`, matching the shipped scrape configuration.
Availability and collection alerts identify the agent by `job` and `instance`;
memory alerts also retain the container `id` and `name`. Health checks are matched
on both `job` and `instance`, so one healthy agent cannot mask another's failure.

Scrapes and rule evaluations run every 30 seconds. A condition first becomes
**pending**, then **firing** once it remains true for its entire `for` duration.
It returns to **inactive** at the next evaluation where the condition no longer
holds. A brief recovery resets the pending timer. Detection includes scrape and
evaluation delay; the durations above start when Prometheus observes the condition,
not at the exact instant a problem begins.

## Optional runtime alerts

After enabling the [OOM/start/PID checks](runtime-checks.md) on an agent built from
version 0.0.68 or newer, mount `examples/monitoring/alerts.runtime.yml` beside
the existing rules and add its path to Prometheus `rule_files`. Keep the existing
availability rules. Use matching agent and UI images tagged 0.0.68 or newer.

This optional file adds OOM detection, three or more starts in a 300-second window,
PID usage above 90% for one minute, and an incomplete-history alert. The frequent
starts rule deliberately selects `window_seconds="300"`; adjust it together with
the agent's `--event-window`. Missing PID limits or disabled checks produce no
corresponding threshold alert. Failed collection is covered by the existing
collection-not-ready alert. Existing notification receivers can deliver these
alerts without a new integration.

## View alerts

Open [Prometheus alerts](http://127.0.0.1:9090/alerts), adjusting the port if you
changed `PROMETHEUS_PORT`. Expand a rule to inspect its labels and diagnostic
annotations. You can also query:

```promql
ALERTS{alertname=~"MonitDocker.*", alertstate="firing"}
```

The rules API at `http://127.0.0.1:9090/api/v1/rules` shows loaded rules, their
health and evaluation errors. The Grafana dashboard remains a metrics dashboard;
these rules are managed by Prometheus, not Grafana Alerting.

**No email, Slack message or other notification is sent by this example.**
To enable delivery, follow the optional [email and Slack examples](notifications.md).
Alert rules never restart containers or execute remediation.

## Agent unreachable

Check `docker compose ps`, the agent logs, and the Prometheus target at
`http://127.0.0.1:9090/targets`. Verify the configured address and network access.
`up` is produced by Prometheus; the agent does not need to respond to report a
scrape failure.

An unreachable agent suppresses collection and memory alerts, avoiding duplicate
reports based on old values. Removing a target from Prometheus configuration
removes it from this alert's scope. These rules also cannot detect the failure
of Prometheus itself; that needs an independent monitor.

## Collection not ready

The agent responds, but has no fresh successful collection. Check `/v1/status`,
`/readyz`, agent logs and Docker socket access. Readiness is false before the
first successful cycle, after a failed cycle, or when the cache exceeds
`--stale-after`. See [serve readiness](serve.md) and [troubleshooting](troubleshooting.md).

The three-minute delay starts when readiness becomes false, which can be later
than the last successful cycle when the cache is aging. A successful scrape
with a missing readiness metric also triggers this alert: it can indicate the
wrong endpoint or incompatible exporter. It is not treated as healthy.

## Container memory high

Inspect the container's workload, memory trend and configured memory limit.
The metric is a percentage of the limit reported by Docker; without an explicit
container limit, that can reflect host memory. Exactly 90% does not trigger the
default rule. See the [memory metric definitions](metrics.md).

Missing memory data is **unknown**, not zero. Stopped containers or unrequested
memory statistics have no memory percentage and produce no memory alert.
When the series disappears, the agent becomes unready, or scraping fails, a
memory alert clears. That does not prove memory usage recovered: check agent
health as well. Once fresh high usage returns, a new five-minute wait begins.
Recreating a container changes its ID and starts a new alert timer.

## Customize and reload

Edit `examples/monitoring/alerts.yml` to change the `> 90` threshold, each `for`
duration or severity. Keep the descriptions consistent with your changes. If
your scrape job has another name, replace every `job="monit-docker"` selector.
For multiple agents, add targets to that job; alerts distinguish their instances.

From `examples/monitoring`, validate and restart Prometheus to apply edits:

```sh
docker compose exec prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose restart prometheus
```

Check the rules API afterward for successful evaluations. Changing only the file
does not reload running rules automatically. Restarting briefly interrupts
scrapes; the named volume retains history.

For an existing Prometheus installation, copy `alerts.yml` next to its config,
add it under `rule_files`, and set `global.evaluation_interval: 30s`, as shown in
`examples/monitoring/prometheus.yml`. Relative rule paths resolve against the
configuration file's directory. Validate and reload through your normal process.

## Regression tests

From the repository root, run the shipped tests with the same pinned Prometheus
version used by Compose:

```sh
docker run --rm --entrypoint promtool \
  -v "$PWD/examples/monitoring:/etc/prometheus:ro" \
  prom/prometheus:v3.5.0 test rules /etc/prometheus/alerts.test.yml
```

The tests simulate time, covering firing boundaries, brief failures, recovery,
missing and stale series, the exact memory threshold, and independent agents and
jobs. CI also validates the configuration and checks that the actual Compose
stack loads and evaluates all three rules and detects a stopped agent.
If you customize thresholds, update the test inputs and expected alert annotations.

See Prometheus's [alerting rule reference](https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/)
and [rule testing guide](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
for the configuration and test syntax.
