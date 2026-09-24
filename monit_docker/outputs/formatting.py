"""Preserve legacy CLI formatting without changing raw measurements."""

import bitmath
from monit_docker.domain.filesystems import FILESYSTEM_BYTES, filesystem_resource


def format_resource(resource, value):
    if value is None:
        return None
    filesystem = filesystem_resource(resource)
    if filesystem:
        return {path: format_resource(filesystem[0], item) for path, item in value.items()}
    if resource in ('mem_usage', 'mem_limit') + FILESYSTEM_BYTES:
        decimals, minimum_kb = 2, False
    elif resource in ('io_read', 'io_write', 'net_rx', 'net_tx'):
        decimals, minimum_kb = 1, True
    else:
        return value
    if value < 1:
        result = bitmath.Byte(value)
    elif minimum_kb:
        result = bitmath.Byte(value).to_kB().best_prefix()
    else:
        result = bitmath.Byte(value).best_prefix()
    return result.format('{value:.%df} {unit}' % decimals).replace('Byte', 'B')
