# Disk space, inode and mount mode checks

Define named groups of paths in the container, then reference the group in a
resource or condition. A group is reusable across container selections. All
paths in a requested group must exist in every selected running container.

```yaml
dir-groups:
  data:
    paths:
      - /data
      - /var/log
  temporary:
    paths:
      - /tmp
      - /dev/shm
conditions:
  data_disk_full:
    expr:
      - disk_percent[data] > 90
  data_inodes_full:
    expr:
      - inode_percent[data] > 95
  data_read_only:
    expr:
      - fs_mode[data] == ro
```

Use one group per path when paths need different thresholds. Group names follow
the same naming convention as condition aliases. Relative paths and control
characters are rejected. Spaces and shell punctuation are passed literally;
stat collection never invokes a shell. The mount-mode probe uses a fixed shell
script with the path as a quoted positional argument. Duplicate paths are sampled once per cycle
and container, including across groups.

Like other configuration sections, `dir-groups` supports templates, variables
and `@import_dir-group` files.

## Read measurements

```sh
monit-docker -c config.yml --name 'app-*' stats \
  --rsc 'disk_percent[data]' --rsc 'disk_available[data]' \
  --rsc 'inode_percent[data]' --rsc 'inode_available[data]'
```

Each resource returns a mapping from path to value. Measurements describe the
**filesystem containing the path**, not the recursive size of the directory.
Two paths on the same filesystem normally report the same capacity. Bind
mounts, volumes and tmpfs are measured as seen inside the container.

| Resource prefix | Value |
| --- | --- |
| `disk_usage` | Used bytes |
| `disk_available` | Bytes available to an unprivileged writer |
| `disk_total` | Total bytes, including reserved blocks |
| `disk_percent` | `used / (used + available) * 100` |
| `inode_usage` | Used inodes |
| `inode_available` | Free inodes |
| `inode_total` | Total inodes |
| `inode_percent` | `used / total * 100` |
| `fs_mode` | Effective mount mode: `ro` or `rw` |

Append `[group]` to every prefix. API values use raw bytes; `stats` formats byte
values for readability. Filesystem `monit --rsc` queries print per-path values;
they do not collapse multiple paths into one percentage exit code.

## Read-only and read-write mounts

```sh
monit-docker -c config.yml --name 'app-*' stats --rsc 'fs_mode[data]'
monit-docker -c config.yml --name 'app-*' monit --dry-run \
  --cmd-if 'fs_mode[data] == ro ? (true)'
```

`fs_mode` supports `==`, `!=`, `in` and `not in` with `ro`/`rw`, using the same
group and same-path matching rules as disk and inode checks. An unavailable mode
is an error (115), never silently `rw`. In the API, unrequested modes are null.

The probe opens each directory for reading and resolves its actual mount ID
using Linux [`fdinfo` and `mountinfo`](https://www.kernel.org/doc/html/latest/filesystems/proc.html).
This follows symlinks and handles bind mounts without guessing from path
prefixes. Either a read-only per-mount flag or a read-only superblock yields
`ro`. A mode-only query needs no stat command or Docker statistics stream and
creates no test file. Only directories are accepted by this probe.

`rw` describes the mount, not the application's ability to create files. Unix
permissions, ACLs, quotas, a full disk and security policy can still prevent
writes. This check does not change permissions or remount anything. Read-only
roots and configuration mounts can be intentional: select the paths expected
to remain writable. Diagnose a filesystem unexpectedly remounted read-only
before choosing a remediation command.

## Evaluate rules

```sh
monit-docker -c config.yml --name 'app-*' monit --dry-run \
  --cmd-if '@data_disk_full ? (true)' \
  --cmd-if '@data_inodes_full ? (true)'
```

Replace `(true)` with an explicitly configured remediation command when ready.
The commands above are a harmless rule preview, not a cleanup procedure.
The same expressions work with `cron` and `serve`, including their existing
cooldowns, trigger delays and action journal.

A rule matches when **any path** in its group satisfies its conditions. When
an alias contains several conditions on the same group, they must all hold on
the **same path**. Conditions on different groups must each find a matching
path. Each rule executes once per container per cycle, regardless of how many
paths match. Separate rules remain independent and may each execute an action.
Trigger delays and cooldowns track the container/rule pair, not individual paths.

Validate paths and group references without contacting Docker:

```sh
monit-docker -c config.yml check-config \
  --cmd-if 'disk_available[data] < 1 GiB ? (true)'
```

This validates configuration syntax, not the existence of paths in containers.

## Continuous monitoring and notifications

```sh
monit-docker -c config.yml --name 'app-*' serve \
  --rsc cpu_percent --rsc mem_percent \
  --rsc 'disk_percent[data]' --rsc 'inode_percent[data]' --rsc 'fs_mode[data]' \
  --rsc 'disk_percent[temporary]' --rsc 'inode_percent[temporary]'
```

Only groups requested by `--rsc` or rules are sampled. Defining a group alone
does not enable collection or add any container exec calls to CPU/memory checks.
`/v1/status` adds a `filesystems` list per container, with `group`, `path` and all
eight numeric values plus `fs_mode`. Uncollected values are null. Prometheus exports these gauges with `id`, `name`, `group`
and `path` labels:

* `monit_docker_container_disk_usage_bytes`
* `monit_docker_container_disk_available_bytes`
* `monit_docker_container_disk_total_bytes`
* `monit_docker_container_disk_usage_percent`
* `monit_docker_container_inode_usage`
* `monit_docker_container_inode_available`
* `monit_docker_container_inode_total`
* `monit_docker_container_inode_usage_percent`
* `monit_docker_container_filesystem_read_only` (1 for `ro`, 0 for `rw`; omitted when not collected)

Add Prometheus alerts to the existing Alertmanager notification pipeline, for
example `monit_docker_container_disk_usage_percent{group="data"} > 90` and
`monit_docker_container_inode_usage_percent{group="data"} > 95`. Use `for` to
choose how long a threshold must remain exceeded before notifying. The `path`
label identifies the affected location; thresholds can also filter by path.

For mount modes, alert on `monit_docker_container_filesystem_read_only{group="data"} == 1`,
with the same freshness checks used for other container metrics.

## Requirements and errors

Capacity collection uses Docker exec and `stat -f -c '%S %b %f %a %c %d' -- PATH` in Linux
containers, compatible with GNU stat and Alpine's BusyBox stat. The container
must provide this command and allow access to the path. Images without stat,
including many distroless images, cannot use this collector. No helper is
installed and no privileged container access or host filesystem mount is needed.

Mount-mode collection instead requires `sh`, `cat`, readable directories and
Linux `/proc/self/fdinfo` and `/proc/self/mountinfo`. It works with Alpine BusyBox
and standard GNU tools. Both probes run with the container's configured exec user.

Output reading is limited to five seconds per probe: 4 KiB for stat and 256 KiB
for mount information. Oversized output is an error. Docker API setup
requests use the client's normal timeout. Closing a timed-out exec connection
does not forcibly kill a stuck probe process inside the container.

A missing/inaccessible path, unsupported stat, invalid output or timed-out read
fails collection with error 115 and container/group/path context. Metric-based
actions are not run from a partial sample; the service reports collection not
ready and withholds stale container metrics. Earlier status-only actions keep
the existing engine ordering and may already have run.

Paused containers cannot execute stat and produce error 115 when a filesystem
check is requested. Stopped containers keep their usual status-only behavior
and have no filesystem samples. Filesystems reporting zero total inodes have
unavailable inode values, not 0% usage: requesting those values fails the check.
