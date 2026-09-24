"""Real cgroup PID accounting, Docker starts and a memory-limited OOM fixture."""
import os
import time
import unittest
import uuid

import docker
from docker.errors import NotFound

from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core import MonitoringEngine

RUNTIME_RESOURCES = ('oom_events', 'starts_recent', 'pids_current', 'pids_percent')
OOM_COMMAND = ('sh', '-c', 'if [ ! -f /tmp/oom-once ]; then touch /tmp/oom-once; '
               'exec tail /dev/zero; fi; exec sleep 120')


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
        obj = self.client.containers.run('alpine:3.20', list(OOM_COMMAND), name=self.name,
                                        detach=True, mem_limit='16m', memswap_limit='16m', pids_limit=16,
                                        restart_policy={'Name': 'on-failure', 'MaximumRetryCount': 1})
        self.objects.append(obj)
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            obj.reload()
            if obj.status == 'running' and obj.attrs['RestartCount'] >= 1:
                break
            time.sleep(0.2)
        else:
            self.fail('Memory-limited fixture did not OOM and restart')
        time.sleep(2)  # The bounded event cutoff is intentionally in the past.
        for _ in range(2):
            item = self.engine().run_once(resources=RUNTIME_RESOURCES).snapshots[0]
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
