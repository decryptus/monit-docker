"""Names and immutable samples for explicitly requested filesystem checks."""

import re
from collections import namedtuple

FILESYSTEM_NUMERIC_FIELDS = ('disk_usage', 'disk_available', 'disk_total', 'disk_percent',
                            'inode_usage', 'inode_available', 'inode_total', 'inode_percent')
FILESYSTEM_FIELDS = FILESYSTEM_NUMERIC_FIELDS + ('fs_mode',)
FILESYSTEM_MODES = ('ro', 'rw')
FILESYSTEM_BYTES = ('disk_usage', 'disk_available', 'disk_total')
GROUP_PATTERN = r'[a-zA-Z][a-zA-Z0-9_.-]{0,64}'
_RESOURCE_RE = re.compile(r'^(%s)\[(%s)\]$' % ('|'.join(FILESYSTEM_FIELDS), GROUP_PATTERN))
FilesystemSample = namedtuple('FilesystemSample', ('group', 'path') + FILESYSTEM_FIELDS, defaults=(None,))


def filesystem_resource(resource):
    match = _RESOURCE_RE.fullmatch(resource)
    return match.groups() if match else None
