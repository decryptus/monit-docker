# HTTP notifications with DWho

Use this example to send or replay a JSON notification to a custom HTTP API using
DWho's notifier registry. It includes a YAML configuration, a Mako JSON template,
a sample Alertmanager payload and a small command-line sender.

`DWhoPushNotifications` loads the YAML and selects `DWhoNotifierHttp` from the
`DWhoNotifiers` registry using the URI scheme (`http` or `https`). Its strict
`send()` method waits for the response and raises on failure. No custom HTTP
client is implemented here.

This is a standalone sender, not an additional Alertmanager receiver service.
For automatic delivery to an API that already accepts Alertmanager webhooks,
configure Alertmanager's native `webhook_configs`. For a custom payload format or
an application already using DWho, the configuration and dispatch shown here
provide a reusable starting point. The existing [Redis receiver](redis-notifications.md)
continues to write to Redis; it does not run this HTTP sender automatically.

## Configure and send

From `examples/monitoring`, install the same pinned dependencies used by the Redis
receiver into a virtual environment (Python 3.12). They include
[dwho 0.3.61 from PyPI](https://pypi.org/project/dwho/0.3.61/), which provides the
strict `send()` API:

```sh
python3.12 -m venv /tmp/monit-dwho-example
/tmp/monit-dwho-example/bin/pip install -r redis-webhook/requirements.txt
umask 077
mkdir -p notifications.local/dwho-http
chmod 700 notifications.local notifications.local/dwho-http
cp dwho-http/http.yml dwho-http/http.json notifications.local/dwho-http/
```

Edit `notifications.local/dwho-http/http.yml`: replace
`https://example.invalid/notifications` with your receiving API's HTTPS URL.
Save the **API's Bearer token** in `notifications.local/dwho-http/token`, using an
editor or secret manager. This is the destination API's credential, separate from
the token used by Alertmanager to authenticate to the Redis receiver.
Keep the credential file private (`chmod 600 notifications.local/dwho-http/token`).
The `notifications.local` directory is already excluded from Git, distribution
archives and Docker contexts.

Send the sample to your configured destination:

```sh
/tmp/monit-dwho-example/bin/python dwho-http/send.py \
  --config-dir notifications.local/dwho-http \
  --token-file notifications.local/dwho-http/token \
  --payload dwho-http/payload.json
```

This command performs a real HTTP request. The shipped URL uses `example.invalid`
and must be replaced. The sample's dates and container name are illustrative.
To replay a saved notification, replace the payload filename; omit `--payload`
(or pass `--payload -`) to read a JSON object from standard input.

## Adapt the payload

The shipped template uses `POST`, `Content-Type: application/json`, a five-second
socket timeout and TLS certificate verification. It preserves the complete input
object, including grouped Alertmanager alerts and their firing/resolved status.
`json.dumps` safely escapes payload strings and the Bearer header.

To wrap the message for a custom API, change the template's `payload` field:

```text
"payload": ${json.dumps({"source": "monit-docker", "event": notification})}
```

`http.json` is a **Mako template producing JSON**, so it is not valid plain JSON
until rendered. Configuration and templates must be trusted administrator files;
Mako templates can execute Python. `http.yml` selects `http.json` relative to the
configuration directory; the script selects only the notification named `http`.
Other configuration files must not overwrite that name.

## Delivery contract and tests

The command exits with `0` only after DWho observes a final HTTP `2xx` response,
and with `1` on configuration, rendering or delivery failure. HTTP redirects
follow the DWho/Requests behavior; configure the final trusted destination URL.
A timeout is a socket inactivity timeout, not a total execution deadline. The
sender has no retry queue or persistence. A timeout can occur after the receiving
API has processed the message, so any retry strategy must account for duplicates.
Acceptance does not prove the receiving application finished processing an event.

Avoid replacing `send()` with the legacy callable: the latter can log errors
without propagating them. Email and Slack remain available through their
[native Alertmanager examples](notifications.md).

CI runs the actual sender, registry and templates against a disposable local HTTP
server. It checks complete firing/resolved payloads, Bearer authentication and
JSON escaping, HTTP `401`/`429`/`500`, timeouts, and rejection before sending when
the payload or template is invalid. No external HTTP destination is contacted.

## Event journal

See the [audit journal guide](audit.md) for persistent events, UTC timestamps,
actors, delivery-result boundaries, JSONL/CSV export and external forwarding.
