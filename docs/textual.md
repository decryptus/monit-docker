# Textual terminal candidate

The optional Textual interface requires Python 3.9+ and the unreleased shared
DWho Textual candidate. Existing commands retain curses as their default.
This feature is available on a review branch, not in the published release.

From this candidate checkout:

```sh
python -m pip install 'git+https://github.com/decryptus/dwho.git@01b802bb33aa02d4dff8a602e27404c5a7e5c21d'
python -m pip install '.[textual]'
monit-docker tui --ui textual
```

Containers and journal events share a searchable table and a scrollable detail panel. The existing Observation service retains its refresh interval, read-only collection and stale-data behavior. Docker selectors and `--rsc` remain unchanged. The journal uses `--audit-file` and shows the latest page, explicitly indicating whether older history exists. This interface does not execute remediation rules or manual actions.

`/` focuses search, Escape clears it, and `q` quits. Resize to at least 80 by
24; 120 columns or more gives the detail panel more room. States have explicit
text as well as color. An accepted or running request is not labelled successful.

## Offline demonstration

```sh
python -m monit_docker.textual_demo
```

This opens the actual interface with labelled synthetic fixtures. It connects
to no service and performs no operation. Screenshots generated from it are
candidate demonstrations, not production observations or release evidence.

See the [contributor validation and media guide](textual-review.md) for tests
and capture generation. The published manual and existing screenshots still
represent the current release until migration is accepted.
