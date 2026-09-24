# File and directory access checks (since 0.0.69)

`fs_readable[group]`, `fs_writable[group]` and `fs_executable[group]` report
kernel access checks as **1 allowed / 0 denied** for an explicitly configured
probe identity. On directories, execute means search/traversal permission.
These checks complement `fs_mode[group]`: a read-write mount can still deny an
application access to a path.

## Explicit identity, no implicit root probe

```yaml
dir-groups:
  app:
    paths: [/data, /etc/app/settings.json]
    access:
      uid: 1000
      gid: 1000
      groups: [1000]
```

```sh
monit-docker -c monit-docker.yml stats --rsc 'fs_readable[app]' --rsc 'fs_writable[app]'
monit-docker -c monit-docker.yml cron --state-file /var/lib/monit-docker/state.json \
  --dry-run --cmd-if 'fs_writable[app] == 0 ? restart'
```

Use numeric container-namespace IDs. `groups` lists **all expected groups,
including the primary GID**, without duplicates. The probe executes through
Docker as `uid:gid`, then verifies its actual UID, GID and group set. It does not
impersonate arbitrary supplementary groups: if Docker supplies different groups,
the check is unavailable. Adjust the container configuration/identity rather
than accepting a misleading success. Effective and permitted capabilities must
both be zero; privileged/root probes with capabilities are rejected.

The group and path identify the sample in JSON and Prometheus. No recursive
scan occurs. One bounded exec checks all three rights for each distinct
path/identity pair in a cycle. Checks are opt-in and do not run for CPU/memory,
disk-space or inode checks alone. `python3` and Linux `/proc/self/status` must be available inside the container.
The isolated standard-library probe uses `os.access` with matching real, effective,
saved and filesystem IDs. It deliberately avoids shell `test`, which may
calculate permission bits without accounting for ACLs (notably BusyBox).
No Python package is installed by the probe; images without Python report the
check as unavailable. Paused containers
cannot run probes. A missing/inaccessible path, missing utilities, identity
mismatch or malformed response fails collection (code 115); an omitted access
identity is a configuration error (110). Unknown results never become 1.

## ACL semantics

The probe delegates permission decisions to the operating system rather than
decoding `ls -l` or reproducing ACL precedence in Python. Linux POSIX ACLs managed
with `setfacl` are exercised in integration tests, including named user/group
entries, ACL masks and directory search rights. A mask such as `mask::r-x`
can remove writing from a named `user:1000:rwx` entry. Default ACLs determine
inheritance by newly created children; they do not grant current access to the
parent directory. Symlinks are followed, and parent-directory search permissions
apply. Only existing regular files and directories are checked.

This is an **access prediction for the probe**, not proof that an application
operation will succeed. No file is opened for writing, created, deleted,
executed or changed; `setfacl` is required only in the test fixture. Quotas,
free space, immutable/append-only flags, read-only mounts, later permission
changes and actual open/create/rename operations can produce different outcomes.
For a directory, creating an entry generally requires both write and search
permission; deleting also involves the sticky bit and the target's ownership.
An executable bit does not prove that a program or interpreter can run.

SELinux/AppArmor may distinguish the probe executable from the application.
Network filesystems (NFSv4 ACLs, SMB server ACLs, credential mapping and caching)
are **not certified by this check**. No NFSv4/SMB ACL parser or full application
security-context reproduction is claimed. Test actual application operations
separately when those semantics matter. A timeout bounds waiting for the Docker
exec response; it does not guarantee cancellation of a kernel-blocked remote
filesystem operation. Prefer bounded, local paths for periodic probes.

## Rules and metrics

Rules accept `== 0`, `!= 0`, `== 1` or `!= 1`, without units or ranges.
Existing directory-group semantics apply: conditions in the same group must
match the same path; any matching path satisfies that group. For “all paths
must be writable”, alert on `fs_writable[app] == 0`, rather than interpreting
`== 1` as a universal assertion. Distinct groups may use different identities
for the same path without sharing cached access results.

Prometheus gauges `monit_docker_container_fs_readable`,
`monit_docker_container_fs_writable` and `monit_docker_container_fs_executable`
carry `id`, `name`, `group` and `path` labels. Unrequested/unavailable fields are
omitted and stale/failed cycles expose no container samples. Rule actions and
their results use the existing audit journal; polling does not add audit events
for every successful permission check.
