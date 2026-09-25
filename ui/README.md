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
`decryptus/monit-docker-ui:X.Y.Z` alongside the matching agent release. The first UI release is **0.0.63**.

The page supports existing API v1 agents in read-only mode when the optional
`manual_actions` capability is absent. Start/stop/restart require the new manual
action API. No route permits shell commands, deletion or configuration changes.
Containers with `manual_actions_protected: true` in their status remain visible
with a Protected lock and disabled controls. The agent enforces the corresponding
`monit-docker.protected` Docker label independently of the browser; automatic
rules remain active. See the [protection guide](../docs/ui.md#protect-a-container-from-manual-actions).

For development only, `ui/tests/browser.cjs` uses Playwright. CI installs the
test tool outside the component, checks multiple viewport sizes and captures
desktop/mobile screenshots using synthetic data. None of those dependencies or
fixtures are copied into the image.

## Journal readability

Since 0.0.71, journal events use contextual alert cards with English action
headings and reasons visible without expanding details. Success is green,
failure or rejection red, skipped actions yellow, queued work and notification
acceptance blue, work in progress purple (since 0.0.72), and simulations neutral. Every color has a visible text label.
Notification acceptance does not imply delivery to a person.

Since 0.0.72, small metadata alerts also distinguish manual actions (blue) from
automatic actions (amber), with neutral light-gray actor and host labels.

Since 0.0.73, compact cards place the action, host and timestamp in the heading,
separated by middle dots, with Event details on the right. Container, source and
actor labels appear below, wrapping on narrow screens.

Since 0.0.74, click a container, Manual action / Automatic action, or result label
to filter the journal. Filters combine, restart at the newest page, and appear
above the events as removable buttons. Clear removes all filters. Container
labels use the full container ID when available, otherwise the name; the API
retains its existing case-insensitive substring matching. Queued and In progress
both select Pending. Exports follow the applied filters and current page.
Quick filters use the applied view and replace unsubmitted form edits.

Since 0.0.75, **View action** opens a separate **Action history** dialog for
action events with a correlation ID. It shows matching action events in recorded
order, oldest first, without applying the main journal filters. Each stage keeps
its source, actor, host, target and reason; a recorded duration is shown in
milliseconds when available. Closing the dialog preserves the journal page and
filters. Notification events and legacy events without an ID have no action link.

The dialog reads one bounded page at a time. **Load older events** explicitly
continues the search, including after an empty page; there is no background scan
or polling. **Refresh action** starts a new snapshot and recovers from expired
cursors. The display is limited to 500 events. Only retained events are shown:
missing stages do not prove an action is still running or that it never completed.
An ID groups recorded events; it does not invent missing lifecycle stages.

The presentation uses the existing CSS and JavaScript, without Bootstrap or
another runtime dependency. Raw fields remain in Event details and exports.

## Product boundary

This is a free, single-host Community UI. A future paid control plane remains
a separate product for multi-host management, teams and durable audit/history.
There are no premium flags or duplicated monitoring rules in this component.

## Screenshots

Synthetic demonstration data, rendered by the browser tests:

![Desktop overview](screenshots/desktop.png)

[Mobile overview](screenshots/mobile.png)

### Live public demo

The deployed demo shows real container metrics, a shared-environment banner and
three protected infrastructure containers. Stopped test fixtures are restored
after about five minutes. This timer belongs to the demonstration host, not the
UI image or agent defaults.

![Public demo on desktop](screenshots/demo-desktop.png)

[Public demo on mobile](screenshots/demo-mobile.png)

Since 0.0.76, opening action history immediately previews matching events from
the current journal page while the agent checks retained history. Refreshing also
keeps the previously displayed events visible. The loading message identifies
this provisional view; a successful response replaces it, including when the
new snapshot is empty. A failed request labels the retained preview as possibly
incomplete or outdated. No extra background requests or persistent browser
storage are used. Ordinary audit text validation also avoids unnecessary
character-by-character work while preserving the canonical escaping rules.
Compiled regular expressions process escape sequences and ASCII control runs;
printable Unicode is preserved, with detailed checks limited to unusual Unicode
runs. Malformed and noncanonical escape sequences are still rejected.
