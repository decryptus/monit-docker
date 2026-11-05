# monit-docker 1.3.1

New installations from the root Compose file build a Docker API proxy. Only
the proxy mounts the daemon socket. The agent can collect container status,
stats and events. Docker exec and start, stop or restart require separate
grants for full container IDs.

## Upgrade from 1.3.0

Keep a copy of your Compose files, configuration and state. The guided
installer detects an existing direct-socket agent and stops before replacing
it. Follow the [migration procedure](https://github.com/decryptus/monit-docker/blob/v1.3.1/docs/compose-quickstart.md#migrate-an-existing-agent)
([français](https://github.com/decryptus/monit-docker/blob/v1.3.1/docs/compose-quickstart-fr.md#migrer-un-agent-existant)).
It covers the socket group, action targets, readiness and rollback. Existing
commands that include the socket-proxy overlay can keep using it. Standalone
Docker commands and separate monitoring or UI examples use their own socket
configuration.
