# monit-docker 1.1.3

After a failed monitoring cycle, the web dashboard shows the last complete
snapshot with its collection time. Controls are hidden on historical rows.
If an automatic action failed, the dashboard identifies the action and the
container when available. Before the first successful cycle there is no history.

## Rules and integrations

- Membership rules and configuration aliases accept spaces around commas,
  for example `status in (paused, running)`.
- During a failed cycle, `/readyz` returns HTTP 503, current containers are
  empty and current per-container metrics are suppressed. API clients can use
  the optional `last_complete_snapshot` field to display historical data.
  A successful cycle restores the live view. See the [HTTP contract](http-api-contract.md).
- With `--trigger-after`, a condition that clears and returns must persist for
  the full delay again. This behavior now has additional regression coverage.

## Upgrade from 1.1.2

Install with `python -m pip install --upgrade 'monit-docker==1.1.3'`.
For the optional terminal interface use `monit-docker[textual]==1.1.3`.
For Docker on Linux amd64, select tag `1.1.3` on `decryptus/monit-docker`
and, when used, `decryptus/monit-docker-ui` on Docker Hub or under
`ghcr.io/decryptus/` using the same image names. Preserve your configuration,
state and journal. No migration is required. See the [Compose quickstart](compose-quickstart.md)
or the [guide en français](compose-quickstart-fr.md).
