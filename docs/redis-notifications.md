# Send Alertmanager notifications to Redis

Use this optional example when another application should consume alerts from a
Redis Stream. The path is Prometheus → Alertmanager → HTTP webhook → Redis.
It complements [email and Slack](notifications.md); it does not change simple
mode, cron, or the dependencies of the monit-docker agent.

The small receiver uses HTTPdis and its sonicprobe server, then calls
`DWhoNotifierRedis.send()` synchronously. It acknowledges a notification only
after Redis returns the ID of the appended entry.

## Start the example

Docker Compose with build support and access to Python package downloads is
required. From `examples/monitoring`, prepare a private directory. For a **new
Redis-only setup**, run:

```sh
umask 077
mkdir -p notifications.local
chmod 700 notifications.local
cp alertmanager.redis.yml notifications.local/alertmanager.yml
od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > notifications.local/redis_webhook_token
printf '%s\n' 'redis://redis:6379/0' > notifications.local/redis_url
# The shared notification overlay requires both files, even when unused.
touch notifications.local/smtp_password notifications.local/slack_webhook_url
chmod 644 notifications.local/alertmanager.yml notifications.local/redis_webhook_token \
  notifications.local/redis_url notifications.local/smtp_password notifications.local/slack_webhook_url
sh start.sh --redis
```

The parent directory is private on the host; individual files are readable by the
unprivileged container user through their mounts. Keep the directory out of git,
images and distribution archives. The repository already excludes it.

If email or Slack is already configured, **keep your existing config and secrets**.
Create only the two Redis secret files, and copy the `webhook_configs` block from
`alertmanager.redis.yml` into the receiver that should also write to Redis. Start
with `--redis` (which also enables the notifications overlay). Each receiver
integration has its own delivery outcome; sending to several destinations is not
an atomic transaction.

The receiver is built locally. Its `requirements.txt` pins HTTPdis, sonicprobe,
redis-py, and [dwho 0.3.61 from PyPI](https://pypi.org/project/dwho/0.3.61/).
This release supplies the strict `send()` and Streams APIs. To update an existing
receiver image after pulling these examples, run from `examples/monitoring`:

```sh
docker compose -f compose.yaml -f compose.notifications.yaml -f compose.redis.yaml \
  build redis-webhook
sh start.sh --redis
```

## Read notifications

Run these commands from `examples/monitoring`:

```sh
docker compose -f compose.yaml -f compose.notifications.yaml -f compose.redis.yaml \
  exec redis redis-cli XRANGE monit-docker:alerts - + COUNT 5
```

Each stream entry contains one field named `payload`: a JSON string containing
**the complete Alertmanager webhook group**, including `version`, `groupKey`,
`status`, `alerts`, labels and annotations. One entry may contain several alerts.
Both `firing` and `resolved` messages are sent. Repeat notifications also produce
entries. Interpret resolution together with [collection health](alerts.md), since
missing container measurements can clear a memory alert.

Consumers may use `XREAD`, or `XREADGROUP` and `XACK` for consumer groups. Consumer
implementation and acknowledgment are separate from this example's HTTP receipt.
Choose an idempotency policy for your consumer: retries after a lost response can
produce duplicates, and a fingerprint alone cannot distinguish firing, resolved
and repeat events.

## Retention and persistence

The stream defaults to `monit-docker:alerts` and the latest **10,000 entries**.
Set `REDIS_STREAM` and `REDIS_STREAM_MAXLEN` in the monitoring `.env` to change
these values, then recreate `redis-webhook`. The limit is exact and must be a
positive integer. Trimming also removes entries that a slow consumer has not
processed. This is an entry count, not a byte or time limit; budget Redis memory
for your payload sizes.

Redis stores AOF data in the `redis-notifications-data` named volume with
`appendfsync everysec`. Normal restarts preserve history; a crash can lose about
one second of writes. A successful HTTP response confirms Redis accepted the
entry, not that it was flushed to disk or processed by a consumer. See
[Redis persistence](https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/).
`docker compose down --volumes` deletes this history.

## Delivery and operation

- `POST /alerts` requires the Bearer token mounted in both Alertmanager and the
  receiver. Keep `max_alerts: 0` to avoid truncation. Requests are limited to 1 MiB.
- The receiver returns `202` with the stream and entry ID after a successful
  append. Redis errors return `503`; [Alertmanager retries failed server requests](https://github.com/prometheus/alertmanager/blob/v0.28.1/notify/webhook/webhook.go).
  Invalid payloads return a client error and require fixing the source/config.
- `/healthz` checks the HTTP process; `/readyz` checks Redis connectivity. Redis
  failure does not stop the process. Delivery resumes when connectivity returns.
- Redis socket connect/read timeouts default to three seconds each; URL query
  parameters can change them to positive values up to five seconds. Redis client
  timeout retries are disabled; Alertmanager owns notification retries.
- Redis and the receiver have no published host ports or Docker socket access.
  The bundled Redis is unauthenticated on an internal Docker network shared only
  with the receiver. Do not expose it publicly. A different Redis service needs
  its own networking, authentication and persistence configuration; `redis_url`
  accepts `redis://` or `rediss://` URLs, including credentials.

After changing files, recreate the services to refresh individually mounted
secrets (both services read the webhook token at startup):

```sh
docker compose -f compose.yaml -f compose.notifications.yaml -f compose.redis.yaml \
  up -d --force-recreate --no-deps redis-webhook alertmanager
```

Use the same three `-f` arguments with `ps`, `logs --tail=100`, or `down`.
Alertmanager remains available on `http://127.0.0.1:9093` by default. Check its
notification failures as well as the receiver's readiness; an outage of the
notification stack cannot reliably report itself through that same stack.

## Regression coverage

CI builds the actual receiver image and runs the three Compose overlays. It
checks Prometheus discovers Alertmanager, grouped firing/resolved delivery,
authentication, malformed/oversized requests, a Redis outage with Alertmanager
retry after recovery, AOF history across a Redis restart, and exact stream
retention. Tests use disposable local services and dummy credentials.

## Event journal

See the [audit journal guide](audit.md) for persistent events, UTC timestamps,
actors, delivery-result boundaries, JSONL/CSV export and external forwarding.
