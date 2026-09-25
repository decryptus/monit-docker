# Journal schema compatibility

This baseline describes the **0.0.78** reader and writer. It defines review rules
for future journal changes; it does not announce a new schema or certify complete
application upgrades. See the [roadmap](roadmap.md) for the remaining installation,
upgrade and 1.0 approval milestones.

## Supported schemas

| Stored record | Current read behavior | Current write/export behavior |
| --- | --- | --- |
| Schema 1, historical raw text | Convert text to schema 2 in memory | Schema 2 |
| Schema 2, canonical visible escapes | Validate text without escaping it again | Schema 2 |
| Mixed schema 1 and 2 files | Process each record by its own version | Schema 2 |
| Missing, non-integer or unknown version | Reject the encountered record | No automatic conversion |

`schema_version` is a per-event integer, separate from the package version and
the HTTP `api_version`. Booleans and `1.0` are not valid version values.
**Published 0.0.65 already writes schema 2 and reads schemas 1 and 2**; schema 1 is
the earlier historical format, not the format of that published release.

No migration script is required for these two schemas. Since 0.0.78, the optional
[`audit-migrate` command](audit-migration.md) can create verified schema 2 copies
with original-byte backups; simulation is the default. Reading does not rewrite,
rename or repair data files. The reader may create the companion lock file.
Ordinary writes and size-based retention can still rotate or remove old events;
read compatibility does not extend retention.

## Event and text rules

The writer emits the fields listed in `monit_docker.audit.FIELDS`. Event identity,
UTC timestamp, host, category, lifecycle event and correlation identify what
happened; optional action/notification fields can be null. Reasons and event
labels remain English. An unknown operation outcome remains unknown: reading an
unmatched `started` entry must never replay the operation or invent a completion.

All string values, including additional fields, use the same reversible visible
escaping convention described in the [audit guide](audit.md#text-encoding-shared-by-all-outputs).
Control characters, non-printable Unicode and leading spreadsheet formula
prefixes are escaped; literal backslashes are doubled. Printable Unicode is
preserved without normalization. JSON adds its own escaping layer. Consumers
display the decoded JSON string **without interpreting its visible escapes**.
Normalizing an already valid schema 2 record must leave its values unchanged.

The reader currently accepts additional scalar fields and does not fill missing
fields. It rejects nested objects/arrays and invalid schema 2 text. This is a
format/text check, not a complete validator of every field's type, timestamp or
business meaning. Consumers must handle absent/null optional values and unknown
event labels without treating them as success.

## Reads and exports

| Interface | Scope and order | Representation |
| --- | --- | --- |
| `AuditJournal.read()` | Configured retained files, oldest first | Normalized schema 2 records |
| `audit-export` | Same snapshot; optional category/date filters | JSONL or fixed-column CSV |
| Private HTTP journal | Bounded snapshot pages, newest first | Normalized records and cursors |
| Private HTTP export | Requested page, with its filters/cursor | JSONL or fixed-column CSV |

JSONL preserves additional scalar fields, event IDs, correlations and numeric/null
types. It is a normalized export, **not a byte-for-byte backup** of the originals.
Exporting valid normalized JSONL again does not add another escaping layer.
CSV includes only `FIELDS`: additional fields are omitted, numbers become text,
and absent/null values become empty cells. CSV is for analysis, not lossless
restoration. There is no CSV import or automatic journal restore command.

Unknown schemas, invalid JSON/text, nested values and truncated records fail the
read when encountered. The full CLI read happens before category/date filtering;
those filters cannot bypass a bad retained event. These failures return exit
**119** from `audit-export`, before it emits event data or a CSV header. Other
export failures, including output I/O failures, can leave partial output: check
the exit code before using an export.

HTTP reads validate the scanned records, including records excluded by filters.
An encountered invalid event returns **503 `audit_unavailable`**, not an empty
successful page. A successful recent page does not validate older unscanned
archives. Cursor expiry, retention and bounded scanning are detailed in the
[HTTP baseline](http-api-contract.md).

Appending checks that the active file ends with a newline; it does not scan all
older records for corruption or unsupported schemas. A successful append is not
an integrity check of retained history. A truncated active tail blocks appends;
complete invalid records can remain undiscovered until read.

## Upgrade and rollback procedure

1. Record the exact package/image versions, journal path and retention settings.
   Stop every writer using the journal, including separate notification adapters
   where applicable. Take a consistent backup of the persistent directories,
   including active and rotated journals, configuration and relevant action state.
   Preserve ownership and permissions; keep the backup outside the rotation path.
2. Test the target reader against a **copy** of that journal with the original
   `--audit-files` and `--audit-max-bytes`. Check the command's exit status and
   compare event IDs/counts and representative text. Do not point an export at
   its own input path: shell redirection would truncate the journal first.
3. Upgrade the agent and matching UI, retain the persistent mounts/settings, and
   verify collection, the journal and exports before resuming normal use.
4. For rollback, stop writers and preserve the post-upgrade files separately.
   Check the older reader against a copy before letting it access the live data.
   Read-time normalization alone leaves original archives unchanged, but newer
   writes, retention, state formats and configuration can still affect rollback.
   Restoring the pre-upgrade backup excludes later events; retain those separately
   for investigation and do not replay their actions.

For example, with a stopped-writer backup copied to `/srv/monit-audit-check` and
the default retention settings, use the **target version's** CLI:

```sh
umask 077
monit-docker --audit-file /srv/monit-audit-check/events.jsonl \
  --audit-max-bytes 5242880 --audit-files 5 \
  audit-export > /srv/monit-audit-check-export.jsonl
```

Use the original limits if different: published 0.0.65 defaults to **10 MiB** per
file, while 0.0.66 and later default to **5 MiB**. Lowering a limit does not shrink
existing archives and can make full reads reject a larger retained file. Reducing
the configured file count excludes higher-numbered archives from reads without
deleting them. A schema test does not certify these operational changes.

On corruption, stop writers and preserve the original bytes before investigation.
A normal export can fail on the damaged journal; retain the files themselves.
No reader silently skips, truncates or repairs events. Recovery decisions require
identifying the damaged range and any resulting history gap explicitly.

## Rules for future changes

- Keep event identity, correlation, field meaning and text encoding unchanged
  within a supported schema. Test compatible additive scalar fields against older
  readers; account for fixed CSV columns and consumers with stricter assumptions.
- A change to encoding, structure or existing semantics requires an explicit
  schema decision. Do not reuse an existing version for incompatible data.
- Before introducing a new schema, publish supported reader/writer versions,
  mixed-history tests and rollback limits. Unknown versions must fail explicitly;
  they must not be guessed, downgraded or discarded silently.
- Prefer read-time adaptation when it can preserve the information. If physical
  conversion becomes necessary, provide a separate explicit tool with a dry run,
  preserved source backup, separate output, count/identity checks and documented
  interruption recovery before enabling the new writer. No such conversion is
  required for the current schemas; `audit-migrate` provides an optional, explicit
  schema 1/2-to-2 conversion without replacing the live journal.

`tests/test_journal_compatibility.py` covers mixed retained schemas across rotation
and page boundaries, CLI and page exports, JSONL round trips, CSV projection and
failures in old archives. Existing audit tests cover escaping, concurrent writes,
rotation, incomplete writes, HTTP errors and forwarding. These are format-level
regressions, not a completed rehearsal of every historical deployment upgrade.
