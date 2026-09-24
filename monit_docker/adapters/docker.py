"""Docker connection, sampling and action execution stay at this boundary."""

import copy
import json
import logging
import os
import sys
from collections import OrderedDict
from numbers import Integral

import docker
from docker.errors import APIError

from monit_docker.core.metrics import ResourceCalculator
from monit_docker.domain.errors import CommandExecutionError, MonitoringError
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.adapters.syntax import DOCKER_COMMANDS
from monit_docker.adapters.filesystems import directory_groups, collect_filesystems
from monit_docker.domain.filesystems import filesystem_resource

LOG = logging.getLogger('monit-docker')

_MANUAL_PROTECTION_LABEL = 'monit-docker.protected'
_UNPROTECTED_LABEL_VALUES = frozenset(('false', '0', 'no', 'off'))


def client_factory(config, name=None, from_env=False):
    """Resolve configuration now, create a fresh connection for each cycle."""
    clients = config.get('clients')
    if from_env or not clients:
        def connect():
            if not os.environ.get('DOCKER_HOST'):
                os.environ['DOCKER_HOST'] = 'unix:///var/run/docker.sock'
            return docker.from_env()
        return connect
    if name and name not in clients:
        raise MonitoringError(110, 'unknown client: %r' % name)
    settings = copy.deepcopy(clients[name or next(iter(clients))]['config'])

    def connect():
        options = copy.deepcopy(settings)
        if isinstance(options.get('tls'), dict) and options['tls']:
            options['tls'] = docker.tls.TLSConfig(**options['tls'])
        return docker.DockerClient(**options)
    return connect


class DockerCollector(object):
    def __init__(self, connect, selector=None, dir_groups=None):
        self.connect = connect
        self.selector = selector or ContainerSelector()
        self.client = None
        self._containers = OrderedDict()
        self.calculator = ResourceCalculator()
        self.dir_groups = directory_groups(dir_groups)

    def begin_cycle(self):
        if self.client is not None:
            raise RuntimeError('Docker collector already has an active cycle')
        self._containers.clear()
        self.client = self.connect()

    def select(self):
        for obj in self.client.containers.list(all=True):
            if self.selector.statuses and obj.status not in self.selector.statuses:
                continue
            # Avoid Docker image lookups unless an image selector needs them.
            tags = obj.image.tags if self.selector.patterns['image'] else ()
            labels = obj.labels.values() if self.selector.patterns['label'] else ()
            if self.selector.matches(obj.id, obj.name, obj.status, labels, tags):
                self._containers[obj.id] = obj
        return tuple(self.describe(identifier) for identifier in self._containers)

    def describe(self, identifier, snapshot=None):
        obj = self._containers[identifier]
        labels = obj.attrs.get('Config', {}).get('Labels') or {}
        protected = (_MANUAL_PROTECTION_LABEL in labels
                     and str(labels[_MANUAL_PROTECTION_LABEL]).strip().lower()
                     not in _UNPROTECTED_LABEL_VALUES)
        values = snapshot.to_dict() if snapshot is not None else {}
        values.update(id=obj.id, name=obj.name, status=obj.status,
                      pid=obj.attrs['State'].get('Pid'),
                      manual_actions_protected=protected)
        return ContainerSnapshot(**values)

    def collect(self, snapshot, resources):
        requested = tuple(dict.fromkeys(filesystem_resource(resource)[1] for resource in resources
                                        if filesystem_resource(resource)))
        if any(group not in self.dir_groups for group in requested):
            raise MonitoringError(110, 'unknown directory group')
        if requested and snapshot.status == 'paused':
            raise MonitoringError(115, 'filesystem checks cannot run in a paused container: %s' % snapshot.name)
        snapshot = self._collect_stats(snapshot, tuple(resource for resource in resources
                                                      if not filesystem_resource(resource)))
        if requested and snapshot.status == 'running':
            values = snapshot.to_dict()
            values['filesystems'] = collect_filesystems(self.client.api, snapshot.id, snapshot.name,
                                                        self.dir_groups, requested)
            for resource in resources:
                filesystem = filesystem_resource(resource)
                if filesystem and any(getattr(sample, filesystem[0]) is None
                                      for sample in values['filesystems'] if sample.group == filesystem[1]):
                    raise MonitoringError(115, 'filesystem metric unavailable for %s: %s' % (snapshot.name, resource))
            snapshot = ContainerSnapshot(**values)
        return snapshot

    def _collect_stats(self, snapshot, resources):
        metrics = tuple(resource for resource in resources if resource not in ('pid', 'status'))
        if snapshot.status not in ('running', 'paused') or not metrics:
            return snapshot
        stream = self._containers[snapshot.id].stats(stream=True)
        try:
            previous = None
            for line in stream:
                current = json.loads(line)
                if previous is None:
                    previous = current
                    continue
                if current['read'] == previous['read']:
                    continue
                values = snapshot.to_dict()
                values.update((resource, self.calculator.get(resource, current, previous))
                              for resource in metrics)
                return ContainerSnapshot(**values)
            raise MonitoringError(115, 'no complete statistics for the container: %s' % snapshot.name)
        finally:
            failed = sys.exc_info()[0] is not None
            try:
                close = getattr(stream, 'close', None)
                if close:
                    close()
            except Exception:
                if not failed:
                    raise
                LOG.exception('stats cleanup failed; preserving the sampling error')

    def end_cycle(self):
        client, self.client = self.client, None
        self._containers.clear()
        if client is not None:
            client.api.close()



class DockerActionExecutor(object):
    def __init__(self, collector):
        self.collector = collector

    def execute(self, identifier, action):
        """Execute against this cycle's selected object, never a cached prior one."""
        obj = self.collector._containers[identifier]
        args, kwargs = copy.deepcopy(action.args), copy.deepcopy(action.kwargs)
        try:
            LOG.info('execute %r on %s', action.command, obj.name)
            if action.kind == 'exec':
                result = obj.exec_run(action.command, *args, **kwargs)
                LOG.info('%r executed in %s: %r', action.command, obj.name, result)
                exit_code = result.exit_code
                if isinstance(exit_code, bool) or not isinstance(exit_code, Integral) or not 0 <= exit_code <= 255:
                    # Detached/streaming execs have no completed status. Never
                    # propagate an unavailable or invalid status as success.
                    return False
                if exit_code:
                    raise CommandExecutionError(exit_code, 'command failed on %s: %r (exit code %s)' %
                                                (obj.name, action.command, exit_code))
                return True
            if action.kind != 'docker' or action.command not in DOCKER_COMMANDS:
                raise ValueError('unsupported Docker action: %r' % (action,))
            getattr(obj, action.command)(*args, **kwargs)
            LOG.info('%s executed on %s', action.command, obj.name)
            return True
        except APIError as error:
            LOG.error('unable to execute %r on %s: %r', action.command, obj.name, error)
            return False
