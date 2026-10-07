# monit-docker 1.1.0

## Optional Textual interface

This version adds an optional read-only terminal dashboard using the shared
DWho 0.3.65 presentation components. Python 3.9+ is required for this extra.
The existing curses interface remains the default.

View containers and journal events with search, scrolling details and
read-only observation. The terminal does not execute remediation or manual actions.

After publication, install with `python -m pip install "monit-docker[textual]==1.1.0"`
and select it with `monit-docker tui --ui textual`.

No configuration or stored-data migration is required. The offline demo uses
explicitly labelled synthetic fixtures and connects to no service. See the
[Textual guide](textual.md) for controls and limitations.
