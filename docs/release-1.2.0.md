# monit-docker 1.2.0

Upgrade the agent and dashboard together. Pin both images to `1.2.0`, or install the Python package with `python -m pip install --upgrade 'monit-docker==1.2.0'`. Keep the existing state volume, audit journal and backups.

## Before restarting the agent

- If you enabled actions, notification ingestion or journal reads, use a different secret for each enabled capability. Reusing credentials can prevent startup. Update the agent and its callers together.
- For separate journal cursors behind an authenticated reverse proxy, enable `serve --trust-proxy-user`, including on read-only installations. Restrict access to the agent and make the proxy overwrite the actor header. Otherwise readers share the anonymous cursor.
- Prometheus access remains open by default. To protect `/metrics`, set `serve --metrics-token-file FILE` and configure the scraper with the matching bearer token. This does not protect `/v1/status`.
- Docker exec now waits up to 60 seconds by default. Set `exec_timeout` (1–86400 seconds) for a command that needs longer. A timeout records an unknown outcome: the command can continue inside the container. Check its result before retrying.

## Docker access

The agent no longer changes the process-wide Docker endpoint. An optional Compose socket proxy restricts Docker API access; existing installations can continue to use their socket mount. See [Compose installation](https://github.com/decryptus/monit-docker/blob/v1.2.0/docs/compose-quickstart.md) for the overlay, required socket group and explicit action targets.
