# Convert a retained journal to schema 2

Available since **0.0.78**. `audit-migrate` is an offline administrative command:
it validates existing journals and can create a **separate conversion bundle**
with byte-for-byte backups. It does not connect to Docker or execute actions.
Normal reads already support schemas 1 and 2, so conversion remains optional.

## Preview, then create the bundle

Stop **all writers** using this journal before applying a conversion, including
notification adapters. Use the original retention settings. Global audit options
go before the subcommand; migration options go after it.

```sh
# Default: validation and a JSON report, without creating output files.
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  --audit-max-bytes 5242880 --audit-files 5 audit-migrate

# Explicit simulation; the planned destination must not already exist.
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  --audit-max-bytes 5242880 --audit-files 5 \
  audit-migrate --dry-run --output-dir /srv/monit-migration-001

# Explicit write, after reviewing the simulation.
monit-docker --audit-file /var/lib/monit-docker/events.jsonl \
  --audit-max-bytes 5242880 --audit-files 5 \
  audit-migrate --apply --output-dir /srv/monit-migration-001
```

The destination's parent must exist and be writable. No existing file or directory
is replaced, including an earlier conversion bundle. Files are mode 0600 and
directories 0700, owned by the user running the command. Reserve disk space for
both the original-byte backup and converted copy; escaped text can grow.

Both modes take the journal lock for a consistent view. A busy lock fails
immediately; the command does not wait indefinitely. Simulation may create the
companion `.lock` file but never changes journal data. Holding the lock protects
against cooperating agent writers; it does not make unrelated administrative file
edits safe. Keep writers stopped until any operator-controlled switch is complete.

## Bundle and verification

| Path in the new directory | Contents |
| --- | --- |
| `backup/events.jsonl` and retained suffixes | Original bytes, including original schema versions |
| `converted/events.jsonl` and retained suffixes | Schema 2 JSONL, preserving file layout and record order |
| `manifest.json` | Final completion marker, counts, settings and SHA-256 checksums |

The command processes only the configured journal and its numbered archives, not
all files in its parent directory. Higher-numbered archives outside `--audit-files`
cause an explicit error rather than a silently incomplete conversion. Adjust the
setting or separately archive those files before proceeding. The maximum supported
retention count remains 100.

The JSON report has `status: "dry_run"` or `"complete"`, `target_schema: 2`, total
`records`, `legacy_records` and a per-file inventory. Each entry reports original
and converted sizes/checksums, schema counts and an ordered identity/correlation
checksum. The manifest has its own `manifest_version: 1`, unrelated to event or
HTTP schema versions. It contains paths and checksums, not event text; protect it
along with the private history.

Validation runs before destination creation. During conversion the source is
checked against that inventory, the original-byte backup is reread, and converted
data is reread to verify schema, count, ordered identities/correlations and the
expected full-content checksum. Only then is the manifest finalized. Additional
scalar fields and null/numeric values are retained. Text follows the existing
[canonical escaping rules](audit.md#text-encoding-shared-by-all-outputs); already
valid schema 2 values are not escaped again. Converting the output again preserves
its normalized bytes. Neither deduplication nor sorting is performed.

The tool streams records; it does not accumulate the full retained history in
memory. Each source/converted record must fit the 64 KiB event limit. Both original
and converted files must fit the configured full-read bound (`--audit-max-bytes`
plus one maximum-size event). Conversion can require a larger limit than the
source because visible escapes expand text. Use that same limit when reading the
converted archive. Published 0.0.65 defaulted to 10 MiB per file, versus 5 MiB since
0.0.66; do not accidentally reduce the limit during this operation.

## Errors and interruption

Exit status is **0** for a successful simulation or completed bundle, **2** for
invalid CLI arguments and **119** for a migration failure. An English diagnostic
is written to stderr on failure; successful reports are JSON on stdout.

Unsupported versions, invalid/noncanonical text, nested fields, truncated or
oversized records, duplicate JSON keys and non-finite numbers are rejected.
Duplicate-key/number checks are deliberately stricter than ordinary historical
reads to avoid silently losing information during physical conversion. Symlink
data/lock files and non-regular files are refused. A nonexistent journal fails;
an existing empty journal is valid and produces an empty converted file.

If a write or verification fails, the original journal remains untouched. The
partial destination is retained for inspection, without a completed manifest.
A process interruption may leave `manifest.json.tmp`. **Do not use a bundle that
has no final `manifest.json` with `status: "complete"`.** Resolve the cause, preserve
the partial output if needed, and rerun with a new destination. There is no
automatic resume, deletion or overwrite of previous attempts. Check exit status
even if files are present; filesystem sync failures are still failures.

## Optional activation and rollback

Creating a bundle does not change the agent's configured path. After verifying
the report and keeping all writers stopped, an operator can point `--audit-file`
to the converted directory or copy the converted set to a prepared persistent
directory. Match retention settings and ownership to the service user. Preserve
the original directory and backup outside the rotation path. Switching paths
invalidates existing journal browsing cursors; reload the first page.

Restart writers only after checking a full export from the chosen path. Do not
concatenate original and converted copies: they contain the same event IDs.
For rollback, stop writers, preserve any events appended after activation and
restore a verified backup with appropriate ownership. Restoring an older backup
does not include those later events and must not replay their actions. This
command converts journal files only, not configuration or action state.

See [journal compatibility](journal-compatibility.md) for the broader upgrade
procedure and [supported environments](supported-environments.md) for platform
and storage requirements.
