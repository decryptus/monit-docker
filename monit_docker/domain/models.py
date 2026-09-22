"""Transport-neutral domain models.

These objects deliberately know nothing about Docker, HTTP, Prometheus or the
command line.  The Community agent and any external control plane can rely on
their serialized shape without importing infrastructure code.
"""

from __future__ import absolute_import

from collections import OrderedDict


class ContainerSnapshot(object):
    """A point-in-time view of one container."""

    FIELDS = ('id', 'name', 'status', 'pid', 'mem_usage', 'mem_limit',
              'mem_percent', 'cpu_percent', 'io_read', 'io_write',
              'net_tx', 'net_rx')

    def __init__(self, **values):
        for field in self.FIELDS:
            setattr(self, field, values.get(field))

    def to_dict(self):
        return OrderedDict((field, getattr(self, field)) for field in self.FIELDS)
