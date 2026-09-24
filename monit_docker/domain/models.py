"""Transport-neutral domain models.

These objects deliberately know nothing about Docker, HTTP, Prometheus or the
command line.  The Community agent and any external control plane can rely on
their serialized shape without importing infrastructure code.
"""

from __future__ import absolute_import

from collections import OrderedDict
import math
from numbers import Integral, Real
from monit_docker.domain.filesystems import FilesystemSample, filesystem_resource

try:
    STRING_TYPES = (basestring,)
except NameError:
    STRING_TYPES = (str,)

FIELDS = ('id', 'name', 'status', 'pid', 'mem_usage', 'mem_limit',
          'mem_percent', 'cpu_percent', 'io_read', 'io_write', 'net_tx', 'net_rx', 'health')
RESOURCE_FIELDS = FIELDS[2:]
STATE_RESOURCES = ('pid', 'status', 'health')
HEALTH_STATES = ('healthy', 'unhealthy', 'starting', 'none', 'unknown')
_POLICY_FIELDS = ('manual_actions_protected',)


class ContainerSnapshot(object):
    """Immutable internal snapshot with raw values, not a frozen wire protocol.

    None means unavailable (including fields not requested by a CLI command).
    CPU percentages may exceed 100 on multi-core hosts. Numerical semantics
    are left to the collector; this model validates types and finite values.
    """

    FIELDS = FIELDS + _POLICY_FIELDS + ('filesystems',)
    RESOURCE_FIELDS = RESOURCE_FIELDS
    __slots__ = FIELDS

    def __init__(self, **values):
        values.setdefault('manual_actions_protected', False)
        values.setdefault('health', 'unknown')
        samples = values.get('filesystems') or ()
        values['filesystems'] = tuple(FilesystemSample(**item) if isinstance(item, dict)
                                     else item for item in samples)
        unknown = set(values) - set(self.FIELDS)
        if unknown:
            raise TypeError('Unknown snapshot fields: %s' % ', '.join(sorted(unknown)))
        for field, value in values.items():
            if field == 'filesystems':
                if not all(isinstance(item, FilesystemSample) for item in value):
                    raise TypeError('Invalid filesystem samples')
                continue
            if field == 'manual_actions_protected':
                if not isinstance(value, bool):
                    raise TypeError('Invalid value for snapshot field: %s' % field)
                continue
            if value is None:
                continue
            if field == 'health':
                valid = isinstance(value, STRING_TYPES) and value in HEALTH_STATES
            elif field in ('id', 'name', 'status'):
                valid = isinstance(value, STRING_TYPES)
            elif field == 'pid':
                valid = isinstance(value, Integral) and not isinstance(value, bool)
            else:
                valid = (isinstance(value, Real) and not isinstance(value, bool)
                         and not math.isnan(value) and not math.isinf(value))
            if not valid:
                raise TypeError('Invalid value for snapshot field: %s' % field)
        for field in self.FIELDS:
            object.__setattr__(self, field, values.get(field))

    def __setattr__(self, name, value):
        raise AttributeError('ContainerSnapshot is immutable')

    def __delattr__(self, name):
        raise AttributeError('ContainerSnapshot is immutable')

    def to_dict(self):
        result = OrderedDict((field, getattr(self, field)) for field in self.FIELDS)
        result['filesystems'] = [item._asdict() for item in self.filesystems]
        return result

    def resource_value(self, resource):
        filesystem = filesystem_resource(resource)
        if filesystem:
            field, group = filesystem
            return OrderedDict((item.path, getattr(item, field)) for item in self.filesystems
                               if item.group == group)
        return getattr(self, resource)
