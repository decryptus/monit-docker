# monit-docker-ui

Optional Community interface for a single monit-docker agent. Static HTML, CSS
and JavaScript are served by Nginx, which also provides TLS, Basic authentication
and a same-origin API proxy. No frontend framework, CDN, font download, Node
runtime or compilation is needed. The UI image has no Docker socket access.

This component is packaged as its own Docker image, not installed into the
Python agent. It lives in the same repository for coordinated contract tests;
its only integration is HTTP API v1. Core/domain code never imports `ui/`.

See the [UI setup guide](../docs/ui.md) for read-only and opt-in action modes.
Build the image with `docker build -t monit-docker-ui:local ui` from the repository
root. The release pipeline builds and tests it separately, then publishes
`decryptus/monit-docker-ui:X.Y.Z` alongside the matching agent release. The first
UI release is pending; build from this branch until it is published.

The page supports existing API v1 agents in read-only mode when the optional
`manual_actions` capability is absent. Start/stop/restart require the new manual
action API. No route permits shell commands, deletion or configuration changes.

For development only, `ui/tests/browser.cjs` uses Playwright. CI installs the
test tool outside the component, checks multiple viewport sizes and captures
desktop/mobile screenshots using synthetic data. None of those dependencies or
fixtures are copied into the image.

## Product boundary

This is a free, single-host Community UI. A future paid control plane remains
a separate product for multi-host management, teams and durable audit/history.
There are no premium flags or duplicated monitoring rules in this component.

## Screenshots

Synthetic demonstration data, rendered by the browser tests:

![Desktop overview](screenshots/desktop.png)

[Mobile overview](screenshots/mobile.png)
