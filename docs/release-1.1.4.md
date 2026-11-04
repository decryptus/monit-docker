# monit-docker 1.1.4

This is the first tagged release with the guided Compose installer for Linux
amd64. On a machine with Git, Docker Engine, Compose v2 and curl, run:

```sh
git clone --branch v1.1.4 --depth 1 https://github.com/decryptus/monit-docker.git
cd monit-docker
./install.sh
```

Press Enter to include the local HTTPS dashboard. First-time dashboard setup
also requires Python 3 and OpenSSL. Choose a username and password, then open
the local address printed after the first collection and HTTPS login check.
The development certificate lasts seven days. To start the agent alone, use
`./install.sh --agent-only`. Repeat installation in the same directory reuses
existing credentials and state.

## Upgrade from 1.1.3

Use the matching 1.1.4 agent and UI images from Docker Hub or GHCR, or install
the Python package with `python -m pip install --upgrade 'monit-docker==1.1.4'`.
Keep the same Compose project, configuration, secrets and volumes. No state,
journal or configuration migration is required. Existing deployments can keep
their current installation method.

[Compose installation and upgrades](compose-quickstart.md) ·
[Installation avec Compose](compose-quickstart-fr.md)
