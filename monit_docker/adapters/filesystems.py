"""Bounded, read-only filesystem probes in the container's mount namespace."""

import re
import socket
import struct
import time

from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.filesystems import GROUP_PATTERN, FilesystemSample, FILESYSTEM_NUMERIC_FIELDS, FILESYSTEM_ACCESS_FIELDS
from monit_docker.adapters.access import access_identities, collect_access

_GROUP_RE = re.compile(GROUP_PATTERN)
_STAT_COMMAND = ('stat', '-f', '-c', '%S %b %f %a %c %d', '--')
_EXEC_TIMEOUT = 5.0
_MAX_OUTPUT = 4096
_MAX_MOUNT_OUTPUT = 256 * 1024
# The user path is always a quoted positional argument, never shell source.
# An opened directory's mnt_id resolves bind mounts and symlinks without prefix
# guessing. cat inherits descriptor 3 and reads its own fdinfo and mount table.
_MODE_COMMAND = ('sh', '-c', 'test -d "$1" || exit 1\nexec 3< "$1" || exit 1\n'
                 'exec cat /proc/self/fdinfo/3 /proc/self/mountinfo', 'monit-fs-mode')
_FRAME_HEADER = struct.Struct('>BxxxI')


def directory_groups(groups):
    result = {}
    for name, entry in (groups or {}).items():
        if not isinstance(name, str) or not _GROUP_RE.fullmatch(name):
            raise MonitoringError(110, 'invalid directory group name')
        paths = entry.get('paths') if isinstance(entry, dict) else None
        if not isinstance(paths, list) or not paths:
            raise MonitoringError(110, 'directory group %s requires a nonempty paths list' % name)
        for path in paths:
            if (not isinstance(path, str) or not path.startswith('/')
                    or any(ord(char) < 32 or ord(char) == 127 for char in path)):
                raise MonitoringError(110, 'directory group %s requires absolute paths without control characters' % name)
        result[name] = tuple(dict.fromkeys(paths))
    access_identities(groups)  # Validate optional identities even before a probe is requested.
    return result


def stat_values(output):
    """df-style percentage: exclude privileged reserved blocks from availability."""
    values = output.decode('ascii').split()
    if len(values) != 6 or any(not item.isdigit() for item in values):
        raise ValueError('invalid stat output')
    size, total, free, available, inodes, ifree = map(int, values)
    if size <= 0 or not 0 <= available <= free <= total or not 0 <= ifree <= inodes:
        raise ValueError('inconsistent stat counters')
    used, iused = total - free, inodes - ifree
    percent = round(100.0 * used / (used + available), 2) if used + available else None
    ipercent = round(100.0 * iused / inodes, 2) if inodes else None
    return (used * size, available * size, total * size, percent,
            iused if inodes else None, ifree if inodes else None,
            inodes if inodes else None, ipercent)


def mount_mode(output):
    """Resolve the opened directory's exact mount, including superblock RO."""
    lines = output.decode('utf-8', errors='strict').splitlines()
    identifiers = [line.split()[1:] for line in lines if line.startswith('mnt_id:')]
    if len(identifiers) != 1 or len(identifiers[0]) != 1 or not identifiers[0][0].isdigit():
        raise ValueError('missing or ambiguous directory mount ID')
    identifier = identifiers[0][0]
    matches = [line.split() for line in lines if line.split()[:1] == [identifier]]
    if len(matches) != 1:
        raise ValueError('directory mount is unavailable or ambiguous')
    fields = matches[0]
    separator = fields.index('-')
    if separator < 6 or len(fields) != separator + 4:
        raise ValueError('invalid mount information')
    modes = [set(options.split(',')) & {'ro', 'rw'} for options in (fields[5], fields[-1])]
    if any(len(mode) != 1 for mode in modes):
        raise ValueError('missing or ambiguous mount mode')
    return 'ro' if any('ro' in mode for mode in modes) else 'rw'


def _exec_stat(api, identifier, path):
    return stat_values(_exec_output(api, identifier, list(_STAT_COMMAND) + [path]))


def _exec_mode(api, identifier, path):
    return mount_mode(_exec_output(api, identifier, list(_MODE_COMMAND) + [path], _MAX_MOUNT_OUTPUT))


def _exec_output(api, identifier, command, max_output=_MAX_OUTPUT, user=None):
    options = {} if user is None else dict(user=user)
    created = api.exec_create(identifier, command,
                              stdout=True, stderr=True, stdin=False, tty=False, **options)
    connection = api.exec_start(created['Id'], socket=True, tty=False)
    transport = getattr(connection, '_sock', connection)
    deadline = time.monotonic() + _EXEC_TIMEOUT
    output = bytearray()
    received = 0

    def read_exact(size, allow_eof=False):
        data = bytearray()
        while len(data) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout('filesystem check timed out')
            transport.settimeout(remaining)
            chunk = transport.recv(size - len(data))
            if not chunk:
                if allow_eof and not data:
                    return None
                raise ValueError('truncated Docker exec frame')
            data.extend(chunk)
        return bytes(data)

    try:
        while True:
            header = read_exact(_FRAME_HEADER.size, allow_eof=True)
            if header is None:
                break
            channel, size = _FRAME_HEADER.unpack(header)
            received += size
            if channel not in (1, 2) or received > max_output:
                raise ValueError('invalid or oversized filesystem probe output')
            content = read_exact(size)
            if channel == 1:
                output.extend(content)
        status = api.exec_inspect(created['Id'])
        if status.get('Running') or status.get('ExitCode') != 0:
            raise ValueError('filesystem probe failed; check the path, permissions and required utilities')
        return bytes(output)
    finally:
        try:
            connection.close()
        finally:
            # Docker may return SocketIO: closing the file wrapper alone does
            # not close its socket owner until garbage collection.
            if transport is not connection:
                transport.close()


def collect_filesystems(api, identifier, container_name, groups, requested, identities=None):
    samples, cache, access_cache = [], {}, {}
    identities = identities or {}
    path_fields = {}
    for group, fields in requested.items():
        for path in groups[group]:
            path_fields.setdefault(path, set()).update(fields)
    for group in requested:
        for path in groups[group]:
            if path not in cache:
                try:
                    fields = path_fields[path]
                    counters = (_exec_stat(api, identifier, path) if fields.intersection(FILESYSTEM_NUMERIC_FIELDS)
                                else (None,) * len(FILESYSTEM_NUMERIC_FIELDS))
                    mode = _exec_mode(api, identifier, path) if 'fs_mode' in fields else None
                    cache[path] = counters + (mode,)
                except Exception as error:
                    raise MonitoringError(115, 'filesystem check failed for %s, group %s, path %s (%s)' %
                                          (container_name, group, path, type(error).__name__)) from error
            access = (None,) * len(FILESYSTEM_ACCESS_FIELDS)
            if set(requested[group]).intersection(FILESYSTEM_ACCESS_FIELDS):
                identity = identities.get(group)
                if identity is None:
                    raise MonitoringError(110, 'access identity is required for directory group: %s' % group)
                key = (path, identity)
                if key not in access_cache:
                    try:
                        access_cache[key] = collect_access(_exec_output, api, identifier, path, identity)
                    except Exception as error:
                        raise MonitoringError(115, 'access check unavailable for %s, group %s, path %s (%s)' %
                                              (container_name, group, path, type(error).__name__)) from error
                access = access_cache[key]
            samples.append(FilesystemSample(group, path, *cache[path], *access))
    return tuple(samples)
