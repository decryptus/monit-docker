# Email and Slack notifications

This optional addition connects the [Prometheus alerts](alerts.md) to Alertmanager
0.28.1. Choose email, Slack, or both. Simple mode and cron remain independent of
this stack. The agent itself sends no notifications.

## Choose an example

From `examples/monitoring`, create a private directory and choose **one** template:

```sh
mkdir -m 700 notifications.local
cp alertmanager.email-slack.yml notifications.local/alertmanager.yml
touch notifications.local/smtp_password notifications.local/slack_webhook_url
chmod 644 notifications.local/*
```

Use `alertmanager.email.yml` for email only, `alertmanager.slack.yml` for Slack
only, or `alertmanager.email-slack.yml` for both. Keep both secret files present;
the unused channel's file can remain empty. These commands are for initial setup;
keep your existing files when restarting or upgrading.

The **directory must remain mode 0700** so other host users cannot traverse it.
Files inside are readable by the container's unprivileged user through individual
bind mounts/Compose secrets. Local Compose secrets are files, not an encrypted
secret store. Keep the directory private, including in backups. Git ignores it
and source packages explicitly exclude it.

## Email: SMTP credentials

Edit `notifications.local/alertmanager.yml`:

| Setting | Value to supply |
| --- | --- |
| `to` | Destination email address |
| `from` | Sender authorized by your SMTP provider |
| `smarthost` | SMTP host and port, usually `smtp.your-provider.example:587` |
| `auth_username` | SMTP login supplied by your provider |

Use your editor to put the SMTP password or application password in
`notifications.local/smtp_password`, as one line. The `auth_password_file` setting
loads it from `/run/secrets/smtp_password`; do not replace it with an inline
password. The example requires **STARTTLS** with certificate verification. Use
an SMTP service supporting STARTTLS, rather than an implicit-TLS-only port.

The placeholders under `example.invalid` must be replaced before use. Provider
requirements for authorized senders, application passwords and rate limits vary.
Alertmanager does not expand shell variables such as `${SMTP_PASSWORD}` inside
its YAML; `.env` is for Compose settings, not receiver secret substitution.

## Slack: incoming webhook

Create or select a Slack app, enable **Incoming Webhooks**, then add a webhook to
your workspace and choose the destination channel. Workspace approval may be
required. See [Slack's official setup guide](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/).

Use your editor to save the resulting HTTPS webhook URL as one line in
`notifications.local/slack_webhook_url`. The example reads it through
`api_url_file`; treat the URL as a credential. The destination is set by the
webhook's Slack app configuration, so the example does not try to override the
channel, username or icon.

## Validate and start

After filling in the chosen receiver's settings, run from `examples/monitoring`:

```sh
GRAFANA_ADMIN_PASSWORD=validation-only docker compose \
  -f compose.yaml -f compose.notifications.yaml run --rm --no-deps \
  --entrypoint /bin/amtool alertmanager check-config /etc/alertmanager/alertmanager.yml
sh start.sh --notifications
```

The temporary `GRAFANA_ADMIN_PASSWORD` value only satisfies Compose's config
interpolation for validation; that command starts no Grafana and changes no
credentials. The startup script generates or reuses your real `.env` normally.

Validation checks syntax, not provider credentials or delivery. Starting with
`--notifications` enables real delivery of firing alerts. Prometheus forwards
alerts to `alertmanager:9093` on the Compose network. The default `sh start.sh`
continues to start only the original monitoring stack.

Open [Alertmanager](http://127.0.0.1:9093) to inspect alerts and create temporary
silences for maintenance. Its UI binds to host loopback; set `ALERTMANAGER_PORT`
in `.env` to change the host port. The `alertmanager-data` volume preserves
silences and notification history across container recreation. Alertmanager has
no Docker socket mount.

Once enabled, keep using `sh start.sh --notifications` and include both Compose
files in lifecycle commands. For example:

```sh
docker compose -f compose.yaml -f compose.notifications.yaml down
```

This preserves volumes. Adding `--volumes` deletes the monitoring history,
Grafana database and Alertmanager state. To disable notifications while keeping
monitoring, stop with both files, then run `sh start.sh` without the flag.

## Message behavior

Each message includes the alert status, name, agent, severity, diagnostic text
and runbook. Multiple affected containers for the same alert and agent are grouped
in one message. Container names/IDs appear in the individual alert descriptions.

| Setting | Default | Effect |
| --- | --- | --- |
| `group_wait` | 30 seconds | Initial delay to collect related firing alerts |
| `group_interval` | 5 minutes | Minimum interval before sending changes to a group |
| `repeat_interval` | 4 hours | Reminder for an unchanged, still-firing group |
| `send_resolved` | true | Send a recovery message after a notified alert resolves |

These delays come **after** the Prometheus rule's `for` duration. A problem that
resolves during the initial group wait may produce no notification. Email and
Slack both receive warnings and critical alerts in the combined example.

To route only critical alerts to email while sending everything to Slack, start
from the combined example, split its receiver into two named receivers (`email`
and `slack`) using the standalone templates, and replace its route with:

```yaml
route:
  receiver: slack
  group_by: [alertname, job, instance]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  routes:
    - receiver: email
      matchers: ['severity="critical"']
      continue: true
    - receiver: slack
```

An inactive or resolved memory alert does not always mean memory recovered: loss
of collection also clears it. Interpret recovery messages with the agent's health
alerts; see [missing-data behavior](alerts.md#container-memory-high).

## Change settings and diagnose delivery

After editing config or secret files, validate as above, then recreate Alertmanager
so individual file mounts are refreshed even if your editor replaced a file:

```sh
docker compose -f compose.yaml -f compose.notifications.yaml up -d \
  --force-recreate --no-deps alertmanager
```

Check `docker compose -f compose.yaml -f compose.notifications.yaml logs --tail=100
alertmanager`, the Prometheus page `http://127.0.0.1:9090/alerts`, and its
`/api/v1/alertmanagers` API. An alert pending in Prometheus is not ready for
notification. Check group delays, active silences and SMTP/Slack delivery errors.
Review logs before sharing them; receiver addresses can appear in diagnostics.

Notification delivery depends on Prometheus, Alertmanager, network access and
the provider. An Alertmanager outage cannot reliably report itself through that
same delivery path. Grouping and retries reduce noise but do not guarantee
exactly-once delivery.

## Redis Streams

To feed another application, add the optional [Alertmanager to Redis receiver](redis-notifications.md).
It can share a receiver with email and Slack, and starts with `sh start.sh --redis`.

## Tests

CI validates the Compose overlay and all receiver configurations, then runs the
real pinned Alertmanager image against local SMTP and Slack-compatible servers.
The tests check authenticated SMTP STARTTLS with certificate validation, grouped
messages, duplicate suppression, and firing/resolved delivery for each of the
three examples. They use no real mailbox, webhook or credentials.

For routing and receiver options, see the
[Alertmanager configuration reference](https://prometheus.io/docs/alerting/latest/configuration/).
