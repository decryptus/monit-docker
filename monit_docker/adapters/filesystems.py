"""Bounded, read-only stat calls in the container's filesystem namespace."""

import re
import socket
import struct
import time

from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.filesystems import GROUP_PATTERN, FilesystemSample

_GROUP_RE = re.compile(GROUP_PATTERN)
_STAT_COMMAND = ('stat', '-f', '-c', '%S %b %f %a %c %d', '--')
_EXEC_TIMEOUT = 5.0
_MAX_OUTPUT = 4096
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


def _exec_stat(api, identifier, path):
    created = api.exec_create(identifier, list(_STAT_COMMAND) + [path],
                              stdout=True, stderr=True, stdin=False, tty=False)
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
            if channel not in (1, 2) or received > _MAX_OUTPUT:
                raise ValueError('invalid or oversized stat output')
            content = read_exact(size)
            if channel == 1:
                output.extend(content)
        status = api.exec_inspect(created['Id'])
        if status.get('Running') or status.get('ExitCode') != 0:
            raise ValueError('stat failed; check the path, permissions and stat availability')
        return stat_values(bytes(output))
    finally:
        connection.close()


def collect_filesystems(api, identifier, container_name, groups, requested):
    samples, cache = [], {}
    for group in requested:
        for path in groups[group]:
            if path not in cache:
                try:
                    cache[path] = _exec_stat(api, identifier, path)
                except Exception as error:
                    raise MonitoringError(115, 'filesystem check failed for %s, group %s, path %s (%s)' %
                                          (container_name, group, path, type(error).__name__)) from error
            samples.append(FilesystemSample(group, path, *cache[path]))
    return tuple(samples)
