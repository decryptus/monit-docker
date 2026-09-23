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

## Enable start, stop and restart

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
across all three commands and agent restarts, including failed attempts after
reservation. `--action-cooldown` can change this (minimum one second).
Autonomous rule cooldowns remain separate. Existing rules remain active and
may reverse a manual stop/start on the next cycle; account for that in rules.
Manual requests do not reset persistent trigger observations.

The API deduplicates request IDs within its last 32 in-memory results. That
history and deduplication disappear at agent restart; this is not an
exactly-once delivery guarantee. The persistent cooldown reduces repeated
attempts. If a network response is lost, the UI does not retry automatically;
inspect recent requests and the actual container state before another action.

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
