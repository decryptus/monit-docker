# monit-docker 1.3.0

The direct commands now read their own optional sections in `monit-docker.yml`.
For example, `stats: {rsc: [cpu_percent, mem_usage], output: json}` selects two
measurements. Running `monit-docker stats --output text` changes the output
format for one invocation.

## Upgrade from 1.2.0

Pin the agent and UI images to `1.3.0`, or install the package with
`python -m pip install --upgrade 'monit-docker==1.3.0'`. Existing command lines
work with their previous defaults. Upgrade before adding the new YAML sections.
Keep existing state, audit files and secrets. For a rollback to 1.2.0, restore
the previous configuration and command line before starting the older agent.

- Each of `serve`, `cron`, `stats` and `monit` reads only its own section. An
  explicit flag overrides that field. Repeated `--rsc` or `--cmd` flags replace
  the corresponding YAML list. `monit --rsc` selects observation even if YAML
  configures an action. Use `--dry-run` to preview rules for one invocation.
- To protect `GET` and `HEAD /v1/status`, set `serve.status_token_file` or pass
  `--status-token-file FILE`. Give clients a dedicated bearer token. When using
  the dashboard, configure its authenticated proxy to inject this token only
  on the upstream status route. Prometheus uses its own metrics credential.

See the [configuration reference](https://github.com/decryptus/monit-docker/blob/v1.3.0/docs/config-cli-contract.md)
and [serve guide](https://github.com/decryptus/monit-docker/blob/v1.3.0/docs/serve.md).
