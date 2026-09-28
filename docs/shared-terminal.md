# Shared terminal presentation

This candidate requires DWho 0.3.63. Release DWho before merging this integration.

CLI JSON serialization is delegated to `dwho.cli.write_json` with the existing
options, streams and exit codes. Business services remain independent of CLI,
HTTP and terminal rendering. Parsing, selection and authorization stay unchanged.

This change adds no ncurses interface and no Redis/configuration requirement.
Local YAML, cron/serve behavior and output contracts remain unchanged.
