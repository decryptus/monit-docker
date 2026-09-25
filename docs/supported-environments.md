# Supported environments and check prerequisites

This is the **0.0.78** support baseline. It separates tested environments from
minimum installation metadata and optional target-container prerequisites.
It does not claim that every Python, Docker or operating-system combination has
been tested. Installation/upgrade rehearsals remain a separate [roadmap](roadmap.md)
milestone.

## Agent, package and images

| Component | Baseline | Evidence and limits |
| --- | --- | --- |
| Agent Python | Python **3.10+**; CI tests **3.10 and 3.12** | Full regression suite on Linux; Python 2 and Python below 3.10 are unsupported |
| Agent operating system | Linux with local persistent storage | Journal/state locking uses `fcntl`; directory sync and no-follow file opens are required; native Windows is unsupported, macOS is not certified |
| Published agent image | Linux image built from `alpine:latest` | The built image runs the regression suite before publication; the Alpine/Python versions are not pinned, so use the release image digest for exact reproduction |
| CPU architecture | CI runner's Linux x86-64 build | Current release workflow does not publish a multi-platform manifest or certify ARM builds |
| Docker daemon | Linux containers, reachable Docker API and permitted operations | CI exercises a real daemon on `ubuntu-latest`; its exact client/server/API, architecture and cgroup versions are printed in the run logs |
| Optional Compose examples | Docker Compose v2 (`docker compose`) | Monitoring/UI examples are validated in CI; legacy `docker-compose` v1 is not certified |
| Optional browser UI | Current browser with JavaScript, fetch and CSS support | Automated desktop/mobile viewport checks use Chromium; mobile layout testing is not a Safari/iOS or Android device certification |

The distribution metadata now declares `Requires-Python: >=3.10`. Older metadata
incorrectly advertised Python 2.7 and obsolete Python classifiers; that was not a
working compatibility guarantee. Upgrade the agent interpreter before installing
0.0.78 on such a host. This does not change the Python requirement of the isolated
access probe inside a target container, described below. Python releases outside
the CI matrix are not separately certified just because the installer accepts them.

There is no currently verified minimum Docker Engine release or complete API
version matrix. The Docker SDK negotiates its connection according to the client
configuration; features must be available on that daemon. Record `docker version`
and the tested release when reporting an issue. Docker Desktop, rootless daemons,
remote TLS endpoints and alternate OCI engines require validation in the actual
deployment; the Linux CI result does not certify all of them.

Dependencies remain in `requirements.txt`; HTTPdis and Sonicprobe remain the
agent foundations, with DWho in the optional HTTP notification integration. The
simple agent does not require Prometheus, Grafana, Redis or a browser. Use matching
agent/UI releases and persist the configured state/journal directories.

## Target-container prerequisites

Python installed in the **agent** is distinct from tools inside the **target**.
Ordinary CPU/memory/status checks do not need Python in every monitored container.

| Check | Target requirement | Missing or unsupported data |
| --- | --- | --- |
| Status and configured health | Docker metadata; health requires a configured Docker healthcheck | No healthcheck is not evidence of a healthy application |
| CPU, memory, network and I/O | Docker statistics and daemon/kernel accounting | Availability depends on the daemon/container state; do not invent zero values |
| Disk space and inodes | Running Linux container, Docker exec, `stat -f -c '%S %b %f %a %c %d' -- PATH` | GNU stat and Alpine BusyBox are exercised; missing tools or inaccessible paths fail collection |
| Read-only mount state | Docker exec, `sh`, `cat`, readable directories, `/proc/self/fdinfo` and `/proc/self/mountinfo` | Paused/stopped containers cannot run probes; failure is explicit |
| Read/write/execute access, including Linux POSIX ACLs | Docker exec, `python3` standard library, `/proc/self/status`, explicit UID/GID/groups with matching IDs and zero effective/permitted capabilities | Missing identity is configuration error 110; probe failures are collection error 115 |
| OOM and recent starts | Docker event API with sufficient retained history | Truncated history produces unknown values; the daemon's history is not durable across restarts |
| PID count/limit | Docker PID statistics and supported kernel/cgroup accounting; running or paused container | Without a finite PID limit, limit/percentage can be null; a rule needing unavailable data fails with 115 |

Access checks use the kernel's permission decision through `os.access`. No package
is installed in the target automatically. `setfacl` is needed to construct ACLs
in test fixtures, not to run the production probe. Distroless images without
the required tools cannot perform the corresponding exec checks. No privileged
container or host filesystem mount is required for these probes.

Linux POSIX ACL coverage does not certify NFSv4/SMB ACLs, SELinux/AppArmor identity
equivalence or future application writes. An access prediction is not proof that
an actual open/create/rename will succeed. See [access checks](access-checks.md),
[filesystems](filesystems.md) and [runtime checks](runtime-checks.md) for limits.

## Storage and optional integrations

State and journals require a writable persistent local filesystem with functioning
file locks, atomic file replacement and `fsync`. Network/distributed filesystem
durability is not certified. Configure volume ownership for the agent user and
mount directories, not just the active journal file. The [migration command](audit-migration.md)
uses the same platform requirements and needs no Docker connection.

The HTTP agent and UI proxy have separate exposure/authentication requirements.
Prometheus/Grafana and Alertmanager examples are optional. Notification adapters
add their own dependencies and transport requirements; follow the corresponding
[notification guide](notifications.md). A successful adapter acknowledgement does
not guarantee delivery to a person.

## Reproduce and report an environment

These read-only commands identify the local runtime and Docker endpoints:

```sh
python3 --version
python3 -m pip show monit-docker docker HTTPdis sonicprobe
docker version
docker compose version
docker info --format 'OS={{.OSType}} Architecture={{.Architecture}} CgroupVersion={{.CgroupVersion}}'
```

For the published image, record its tag **and digest**, along with the target
container image, requested checks, cgroup version and relevant filesystem type.
Do not include tokens, credentials or full private configuration in bug reports.
The CI logs record the exercised environment; they do not freeze future
`ubuntu-latest` or `alpine:latest` versions. New platform claims require tests before
they are added to this baseline.
