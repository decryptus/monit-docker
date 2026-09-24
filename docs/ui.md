# Optional lightweight interface

`monit-docker-ui` is a separate, optional Community component. The Python package
and the simple/cron mode do not include a frontend. An agent in `serve` mode
supplies API v1; Nginx serves the UI and authenticates its API requests.

The desktop layout shows a compact container list; on mobile each container is
a card with touch-sized buttons. It displays current status, CPU, memory,
collection health and cumulative rule-action counters. It has no historical
graphs: use [Grafana](grafana.md) for those. The last 32 manual request results
are a temporary operational view, not a durable audit log.

The browser refreshes every five seconds while visible. It hides measurements
and disables controls if no successful status response arrives for ten seconds,
including when automatic refresh is paused. Unknown metrics display a dash, not
zero; CPU usage can exceed 100% on multiple cores.

Available since **0.0.63**. The commands below use matching versioned images
for the agent and the optional UI.

## Try the published release in an isolated test

The **Published UI acceptance** GitHub Actions workflow pulls a matching agent/UI
release from Docker Hub without rebuilding either image. It creates a temporary
Compose project, credentials, certificate and one Alpine demonstration container.
The agent's name selector permits actions only on that container. The runner tests
read-only access first, then enables manual controls and exercises cancellation,
Stop, Start and Restart through Chromium at desktop and mobile viewport sizes.
Actual Docker state is checked after each action; the API is not mocked.

For a quicker test, the overlay uses a one-second collection interval and manual
cooldown. These are test settings; the documented installation below retains its
30-second defaults. The agent port stays private and HTTPS binds only to loopback.
The temporary stack and state volume are removed when the test finishes.

The workflow artifact `published-ui-acceptance` contains desktop/mobile screenshots
and a report with the image tags and pulled digests. It contains no credentials or
browser traces. This is an automated test environment, not a persistent dashboard
URL. Once the workflow is on the default branch, **Run workflow** accepts a
published `X.Y.Z` version.

To reproduce on a disposable Linux Docker host with Python 3, OpenSSL, Node and
Playwright Chromium installed:

```sh
MONIT_UI_RELEASE=0.0.66 \
PLAYWRIGHT_MODULE=/path/to/node_modules/playwright \
UI_SCREENSHOTS=/tmp/monit-ui-release-results \
python3 .github/scripts/check-ui-release.py
```

## Start with read-only access

Requirements: Docker Compose v2, Python 3 and OpenSSL on the host. From the
repository root:

```sh
cd examples/ui
python3 prepare.py --self-signed
docker compose up -d
```

The script asks for a username and password without echoing the password. It
creates private local files and refuses to overwrite an existing directory.
Open **https://localhost:8443/** and authenticate using those credentials. The
self-signed certificate is only for a local trial, expires after seven days,
and causes a browser trust warning. No port 80 or unencrypted login is exposed.

For a real hostname, run `python3 prepare.py` without `--self-signed`, then put
your trusted certificate chain in `secrets.local/tls.crt` and its private key in
`secrets.local/tls.key`. Keep the directory and key private. Certificates are
provided by the operator; this example does not automate ACME or renewal.

To make the HTTPS proxy reachable on a chosen host interface, create a local
`.env` file, for example:

```text
UI_BIND=192.0.2.10
UI_PORT=443
```

Replace the documentation address with an actual address on your host and use
a certificate for your hostname. The default is loopback only. The agent's
port **9808 is never published** by this Compose example. Nginx is the only
component exposed to the browser.

## Enable manual actions

For a new installation, prepare the additional proxy secret:

```sh
cd examples/ui
python3 prepare.py --actions --self-signed
```

If you already prepared read-only credentials, stop the example, move
`secrets.local` to a private backup location outside the repository, then rerun
the command. For a real hostname, omit `--self-signed` and supply the trusted
certificate/key as above. Never commit or distribute the generated directory.

Set the exact browser origin, including a nonstandard port, in `.env`:

```text
UI_ORIGIN=https://localhost:8443
```

Then start the action overlay:

```sh
docker compose -f compose.yaml -f compose.actions.yaml up -d
```

Opening `https://127.0.0.1:8443` when the origin is configured as
`https://localhost:8443` will allow reads but reject commands. Set the exact
origin you intend to use; do not add a trailing slash. There is no wildcard.

The UI requires confirmation before submitting a command. The server permits:

| Action | Accepted current container state |
| --- | --- |
| Start | `created`, `exited` |
| Stop | `running`, `restarting` |
| Restart | `running` |
| Rearm auto restarts | `created`, `running`, `paused`, `restarting`, `exited`, `dead`; a recorded automatic restart attempt is required |

**Rearm auto restarts** appears when a counter is nonzero and the agent advertises
support. Its confirmation shows the count being cleared and explains that
matching rules may restart the container on the next cycle. The reset itself
executes no Docker command and preserves automatic cooldowns and trigger timers.
It uses the same authenticated queue, full-ID selection, protection checks and
audit journal as the other actions. After success, the next snapshot refreshes
the counter and the button disappears. See [restart budgets](restart-limit.md).

All authenticated users have the same permissions. No user database, roles,
shell execution, removal, pause or configuration editing is provided. The
browser's Basic authentication credentials are managed by Nginx; logging out
may require closing the browser's authenticated session.

An accepted request is **queued**, not proof that Docker completed the action.
Follow its outcome under Manual activity. A slow monitoring cycle delays the
command; an unstarted request expires after 60 seconds. There is at most one
queued or running request. The next monitoring cycle refreshes measurements
after the attempt. There is no forced timeout or cancellation of an active
Docker call, and shutting down the agent discards queued requests.

The agent reselects the target using its full container ID and the configured
selectors immediately before execution. A replacement with the same name is
not targeted. A persistent **30-second per-container manual cooldown** applies
across all manual commands, including rearm, and agent restarts, including failed attempts after
reservation. `--action-cooldown` can change this (minimum one second).
Autonomous rule cooldowns remain separate. Existing rules remain active and
may reverse a manual stop/start on the next cycle; account for that in rules.
Manual requests do not reset persistent trigger observations.

The API deduplicates request IDs within its last 32 in-memory results. That
history and deduplication disappear at agent restart; this is not an
exactly-once delivery guarantee. The persistent cooldown reduces repeated
attempts. If a network response is lost, the UI does not retry automatically;
inspect recent requests and the actual container state before another action.

## Protect a container from manual actions

Add this Docker label to a container that should stay visible in monitoring but
must not accept manual Start, Stop, Restart or Rearm requests:

```yaml
services:
  database:
    labels:
      monit-docker.protected: "true"
```

Apply the label through your normal container deployment. Editing a Compose file
alone does not change a running container's labels; recreation is required.
The UI displays a **Protected** lock and disables that container's controls.
The agent rejects authenticated direct API requests too, and checks fresh
container metadata again before executing an already queued request. Rejections
do not reserve a manual cooldown.

**Only manual actions are blocked.** Monitoring, metrics, notifications and
automatic rules in `serve`, `cron` or `monit` retain their existing behavior.
This label is not a Docker permission boundary: it does not restrict an operator
using the Docker CLI or another service with Docker socket access.

Without the label, existing manual behavior is unchanged. Use `"false"` to opt
out explicitly (`"0"`, `"no"` and `"off"` are also accepted, ignoring case and
surrounding whitespace). Any other value, including an empty value or typo,
protects the container. Prefer the quoted `"true"` / `"false"` spelling above.

This feature is available since **0.0.64** in both the agent and UI. The earlier
`0.0.63` images do not enforce this label. Older UIs may
still show enabled buttons, but the upgraded agent rejects protected actions.

## Authentication and network boundaries

Nginx protects the HTML, assets, status API and command endpoint with Basic
authentication over TLS. The browser never receives the proxy's action secret.
Nginx replaces any caller-supplied `X-Monit-Action-Token` with the configured
secret. The agent requires that secret plus the exact allowed HTTPS `Origin`
and a bounded JSON request. It does not trust a forwarded username. A cross-site
form cannot submit an accepted command. No CORS permission is granted.

The agent itself still has no read authentication or TLS. Only the private
network can reach its status/metrics endpoints. Its write API is disabled
unless `--allow-actions` is set together with `--action-origin`,
`--action-token-file` and `--state-file`. Combining it with `--dry-run` is an
error. The token file contains 64 lowercase hexadecimal characters; generate
it using a cryptographic random source. Rotate the token on both the agent and
proxy, then restart both. Use one state file when coordinating with cron.

Nginx serves only `/`, `/app.css`, `/app.js`, `/v1/status` and `/v1/actions`.
`/metrics`, `/healthz`, `/readyz` and other paths are not forwarded publicly.
The page uses a restrictive Content Security Policy and treats names as text.
All frontend assets are local and require no third-party network requests.

Only the agent mounts the Docker socket. A read-only socket mount does not
restrict Docker API privileges. Limit the agent's container selectors when
you do not want all containers to be eligible for manual operations.

## Prometheus

Prometheus uses **http://monit-docker:9808/metrics** on the private `agent`
network, without the browser's authentication. There is no second agent port.
For example, add this file as another Compose overlay:

```yaml
services:
  prometheus:
    image: prom/prometheus:v3.5.0
    networks: [agent]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
```

Create `prometheus.yml` alongside it:

```yaml
global:
  scrape_interval: 15s
scrape_configs:
  - job_name: monit-docker
    static_configs:
      - targets: ['monit-docker:9808']
```

Do not run the existing full monitoring Compose stack as well unless you
intend to run a second agent. You can instead adapt its Prometheus/Grafana
services to this stack. If an agent needs a remote Docker API rather than a
local socket, review the private network's routing before changing the example.

## Updates and validation

Update both image tags in the Compose file, pull the images and recreate both services. Nginx
resolves the agent's Docker DNS address when it starts, so restart Nginx when
recreating the agent. After replacing a certificate or credentials, recreate
the UI container to reread the mounted files:

```sh
docker compose -f compose.yaml -f compose.actions.yaml pull
docker compose -f compose.yaml -f compose.actions.yaml up -d --force-recreate
```

Use only `compose.yaml` for read-only installations. To disable writes, recreate
the agent without the actions overlay. Keep the `state` volume if you need its
cooldowns; `down --volumes` deletes it.

The release workflow tests the Nginx image for authentication, TLS, origin
checks, token replacement, direct-write rejection and private metrics. Browser
tests cover mobile/desktop rendering, literal container names, confirmation,
offline/stale data and ambiguous responses. The `ui-screenshots` CI artifact
contains screenshots generated from synthetic demonstration data.

Nginx configuration reference: [Basic authentication](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html),
[HTTPS](https://nginx.org/en/docs/http/configuring_https_servers.html) and
[proxy headers](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_set_header).

## Public demo

Try [the shared public demo](https://demo.monit-docker.com/) without installing
anything. Actions operate real containers and are visible to other visitors.
Only `monit-demo-web` and `monit-demo-worker` can be controlled; the agent,
interface and HTTPS proxy remain visible and protected from manual actions.

Stopped test containers are automatically started after five minutes, checked
once per minute by a timer on the demo host. Running containers are left alone;
containers, volumes and recent manual activity are not cleared. This recovery is
specific to the public demonstration and is not enabled on your own installation.
Its starts are recorded in the host journal, outside the manual request history.

Your own UI installation uses the credentials, trusted proxy and exact action
origin described in this guide.

For persistent manual and automatic action history and authenticated actor
attribution, see the [audit journal guide](audit.md).


## Persistent journal page (since 0.0.66)

The optional **Journal** link opens `/logs` when private audit reading is enabled.
It provides filters, event details, bounded pagination and CSV/JSONL exports of
the displayed page. It does not poll in the background. See the
[audit guide](audit.md) for explicit
activation, a separate proxy secret, versioned Compose wiring and retention.
The public demo keeps its journal private.
