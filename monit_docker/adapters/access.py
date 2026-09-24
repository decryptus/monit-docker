"""Non-mutating Linux access probes; let the kernel evaluate POSIX ACLs."""

from monit_docker.domain.errors import MonitoringError

_IDENTITY_FIELDS = frozenset(('uid', 'gid', 'groups'))
_MAX_ID = 4294967294
_MAX_GROUPS = 128
_ACCESS_COMMAND = ('python3', '-I', '-S', '-c', '''import os, stat, sys
if sys.platform != 'linux':
    raise RuntimeError('Linux is required')
with open('/proc/self/status') as stream:
    status = dict(line.split(':', 1) for line in stream if ':' in line)
if (len(set(map(int, status['Uid'].split()))) != 1
        or len(set(map(int, status['Gid'].split()))) != 1):
    raise RuntimeError('real, effective, saved and filesystem IDs must agree')
mode = os.stat(sys.argv[1]).st_mode
kind = 'directory' if stat.S_ISDIR(mode) else 'file' if stat.S_ISREG(mode) else None
if kind is None:
    raise RuntimeError('only regular files and directories are supported')
print('monit-access-v1')
print(os.getuid())
print(os.getgid())
print(' '.join(map(str, sorted(set(os.getgroups()) | {os.getgid()}))))
print('CapPrm:' + status['CapPrm'].strip())
print('CapEff:' + status['CapEff'].strip())
print(kind)
for flag in (os.R_OK, os.W_OK, os.X_OK):
    print(int(os.access(sys.argv[1], flag)))
''')


def access_identities(groups):
    result = {}
    for group, entry in (groups or {}).items():
        identity = entry.get('access') if isinstance(entry, dict) else None
        if identity is None:
            continue
        if (not isinstance(identity, dict) or set(identity) != _IDENTITY_FIELDS
                or not _valid_id(identity['uid']) or not _valid_id(identity['gid'])
                or not isinstance(identity['groups'], list)
                or not 1 <= len(identity['groups']) <= _MAX_GROUPS
                or any(not _valid_id(value) for value in identity['groups'])
                or identity['gid'] not in identity['groups']
                or len(set(identity['groups'])) != len(identity['groups'])):
            raise MonitoringError(110, 'access identity requires numeric uid, gid and a distinct groups list including gid: %s' % group)
        result[group] = (identity['uid'], identity['gid'], tuple(sorted(identity['groups'])))
    return result


def _valid_id(value):
    return type(value) is int and 0 <= value <= _MAX_ID


def access_values(output, identity):
    lines = output.decode('ascii').splitlines()
    if len(lines) != 10 or lines[0] != 'monit-access-v1':
        raise ValueError('incomplete access probe output')
    uid, gid, groups = identity
    if (lines[1] != str(uid) or lines[2] != str(gid)
            or not lines[3].split() or any(not x.isdigit() for x in lines[3].split())
            or tuple(sorted(set(map(int, lines[3].split())))) != groups):
        raise ValueError('access probe identity or supplementary groups differ from configuration')
    capabilities = dict(line.split(':', 1) for line in lines[4:6])
    if set(capabilities) != {'CapEff', 'CapPrm'} or any(int(value.strip(), 16) != 0 for value in capabilities.values()):
        raise ValueError('privileged access probe cannot model an unprivileged application')
    if lines[6] not in ('file', 'directory') or any(value not in ('0', '1') for value in lines[7:]):
        raise ValueError('invalid access probe result')
    return tuple(map(int, lines[7:]))


def collect_access(run, api, identifier, path, identity):
    return access_values(run(api, identifier, list(_ACCESS_COMMAND) + [path],
                             user='%d:%d' % identity[:2]), identity)
