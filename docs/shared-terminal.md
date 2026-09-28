# Shared terminal presentation

This integration requires the published DWho 0.3.63 or later.

CLI JSON serialization is delegated to `dwho.cli.write_json` with the existing
options, streams and exit codes. Business services remain independent of CLI,
HTTP and terminal rendering. Parsing, selection and authorization stay unchanged.

This change adds no ncurses interface and no Redis/configuration requirement.
Local YAML, cron/serve behavior and output contracts remain unchanged.

The explicit read-only [terminal interface](terminal.md) is being developed
separately. Ordinary commands never activate curses automatically.
