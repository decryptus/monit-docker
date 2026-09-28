"""Real cgroup PID accounting, Docker starts and a memory-limited OOM fixture."""
import json
import os
import time
import unittest
import uuid
from unittest.mock import patch
from monit_docker.adapters import events as event_adapter

import docker
from docker.errors import NotFound

from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core import MonitoringEngine

RUNTIME_RESOURCES = ('oom_events', 'starts_recent', 'pids_current', 'pids_percent')
OOM_COMMAND = ('sh', '-c', 'if [ ! -f /tmp/oom-once ]; then '
               'while [ ! -f /tmp/trigger-oom ]; do sleep 0.1; done; '
               'touch /tmp/oom-once; exec tail /dev/zero; fi; exec sleep 120')


@unittest.skipUnless(os.environ.get('MONIT_DOCKER_INTEGRATION') == '1', 'requires opt-in Docker daemon')
class RuntimeDockerTests(unittest.TestCase):
    def setUp(self):
        self.client = docker.from_env(timeout=15)
        self.addCleanup(self.client.close)
        self.objects = []
        self.addCleanup(self.cleanup)
        self.name = 'monit-runtime-test-' + uuid.uuid4().hex

    def cleanup(self):
        for obj in self.objects:
            try:
                obj.remove(force=True)
            except NotFound:
                pass

    def engine(self):
        collector = DockerCollector(lambda: docker.from_env(timeout=15),
                                    ContainerSelector(selectors={'name': [self.name]}))
        return MonitoringEngine(collector, DockerActionExecutor(collector))

    def test_oom_event_survives_automatic_restart_and_new_agent(self):
        evidence = dict(started_at=time.time(), queries=[])
        original = event_adapter.read_history

        def capture(api, until):
            query = dict(until=until, requested_at=time.time())
            evidence['queries'].append(query)
            try:
                records = original(api, until)
                query.update(returned_at=time.time(), total_events=len(records),
                             fixture_events=[event for event in records
                                 if event.get('Actor', {}).get('Attributes', {}).get('name') == self.name])
                return records
            except Exception as error:
                query['error'] = type(error).__name__
                raise

        try:
            with patch.object(event_adapter, 'read_history', side_effect=capture):
                self.exercise_oom(evidence)
        finally:
            # Diagnostic failure must never replace the original assertion.
            try:
                evidence['finished_at'] = time.time()
                version = self.client.version()
                evidence['docker_version'] = version.get('Version')
                evidence['runtime_components'] = version.get('Components')
                evidence['containers'] = []
                for obj in self.objects:
                    obj.reload()
                    evidence['containers'].append(dict(
                        id=obj.id, state=obj.attrs.get('State'),
                        restart_count=obj.attrs.get('RestartCount'),
                        memory=obj.attrs.get('HostConfig', {}).get('Memory'),
                        memory_swap=obj.attrs.get('HostConfig', {}).get('MemorySwap'),
                        logs=obj.logs(tail=20).decode('utf-8', 'replace')[-4096:]))
                until = int(time.time()) - 1
                late = original(self.client.api, until)
                evidence['late_history'] = dict(until=until, total_events=len(late),
                    fixture_events=[event for event in late
                        if event.get('Actor', {}).get('Attributes', {}).get('name') == self.name])
            except Exception as error:
                evidence['diagnostic_error'] = type(error).__name__
            print('OOM_DIAGNOSTICS ' + json.dumps(evidence, sort_keys=True), flush=True)

    def exercise_oom(self, evidence):
        obj = self.client.containers.run('alpine:3.20', list(OOM_COMMAND), name=self.name,
                                        detach=True, mem_limit='16m', memswap_limit='16m', pids_limit=16,
                                        restart_policy={'Name': 'on-failure', 'MaximumRetryCount': 1})
        self.objects.append(obj)
        # Starting the allocation in PID 1 immediately can precede the runtime's
        # OOM watcher registration. Complete start and an exec round trip first;
        # never substitute an exit-137 inference for a real Docker OOM event.
        obj.reload()
        self.assertEqual(obj.status, 'running')
        self.assertEqual(obj.attrs['RestartCount'], 0)
        ready = obj.exec_run(['sh', '-c', 'test ! -f /tmp/oom-once'])
        self.assertEqual(ready.exit_code, 0, ready.output)
        evidence['trigger_requested_at'] = time.time()
        trigger = obj.exec_run(['touch', '/tmp/trigger-oom'])
        self.assertEqual(trigger.exit_code, 0, trigger.output)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            obj.reload()
            if obj.status == 'running' and obj.attrs['RestartCount'] >= 1:
                break
            time.sleep(0.2)
        else:
            self.fail('Memory-limited fixture did not OOM and restart')
        evidence['restart_observed_at'] = time.time()
        evidence['restart_state'] = obj.attrs.get('State')
        time.sleep(2)  # The bounded event cutoff is intentionally in the past.
        records = event_adapter.read_history(self.client.api, int(time.time()) - 1)
        self.assertTrue(any(record.get('Action') == 'oom'
                            and record.get('Actor', {}).get('ID') == obj.id
                            for record in records),
                        'Fixture restarted without a retained Docker OOM event')
        for _ in range(2):
            item = self.engine().run_once(resources=RUNTIME_RESOURCES).snapshots[0]
            evidence.setdefault('snapshots', []).append(item.to_dict())
            self.assertEqual(item.event_history_complete, 1)
            self.assertGreaterEqual(item.oom_events, 1)
            self.assertGreaterEqual(item.starts_recent, 2)
            self.assertGreaterEqual(item.pids_current, 1)
            self.assertEqual(item.pids_limit, 16)

    def test_pid_counts_manual_restart_and_stopped_history(self):
        obj = self.client.containers.run('alpine:3.20', ['sleep', '120'], name=self.name,
                                        detach=True, pids_limit=20)
        self.objects.append(obj)
        obj.restart(timeout=1)
        time.sleep(2)
        item = self.engine().run_once(resources=RUNTIME_RESOURCES).snapshots[0]
        self.assertEqual(item.starts_recent, 2)
        self.assertEqual(item.oom_events, 0)
        self.assertEqual(item.pids_limit, 20)
        self.assertGreaterEqual(item.pids_current, 1)
        self.assertEqual(item.pids_percent, round(100 * item.pids_current / 20, 2))
        obj.stop(timeout=1)
        time.sleep(2)
        stopped = self.engine().run_once(resources=RUNTIME_RESOURCES).snapshots[0]
        self.assertEqual(stopped.status, 'exited')
        self.assertEqual(stopped.starts_recent, 2)
        self.assertIsNone(stopped.pids_current)


if __name__ == '__main__':
    unittest.main()
