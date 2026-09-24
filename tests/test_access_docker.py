"""Real Linux POSIX ACLs via setfacl, tested as a non-root container user."""
import os
import unittest
import uuid

import docker
from monit_docker.adapters.docker import DockerCollector, DockerActionExecutor
from monit_docker.adapters.selection import ContainerSelector
from monit_docker.core import MonitoringEngine
from monit_docker.domain.errors import MonitoringError

_SETUP = '''set -eu
apk add --no-cache acl >/dev/null
mkdir -p /checks/acl /checks/default /checks/group /checks/no-search
chmod 755 /checks
chmod 700 /checks/*
setfacl -m u:1000:rwx,m::r-x /checks/acl
setfacl -m d:u:1000:rwx,d:m::rwx /checks/default
setfacl -m g:1000:rwx /checks/group
setfacl -m u:1000:rw- /checks/no-search
touch /checks/acl/file
chmod 600 /checks/acl/file
setfacl -m u:1000:rw-,m::r-- /checks/acl/file
ln -s /checks/acl/file /checks/link
'''


@unittest.skipUnless(os.environ.get('MONIT_DOCKER_INTEGRATION') == '1', 'requires opt-in Docker daemon')
class AccessDockerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = docker.from_env(timeout=60)
        cls.addClassCleanup(cls.client.close)
        cls.name = 'monit-access-test-' + uuid.uuid4().hex
        cls.obj = cls.client.containers.run('alpine:3.20', ['sleep', '600'], name=cls.name, detach=True, pids_limit=32)
        cls.addClassCleanup(cls.obj.remove, force=True)
        result = cls.obj.exec_run(['sh', '-c', _SETUP])
        if result.exit_code:
            raise RuntimeError('ACL fixture setup failed: ' + result.output.decode())

    def snapshot(self, paths, identity=None):
        group = dict(paths=paths, access=identity or dict(uid=1000, gid=1000, groups=[1000]))
        collector = DockerCollector(lambda: docker.from_env(timeout=15),
                                    ContainerSelector(selectors={'name': [self.name]}), {'app': group})
        engine = MonitoringEngine(collector, DockerActionExecutor(collector))
        return engine.run_once(resources=('fs_readable[app]', 'fs_writable[app]', 'fs_executable[app]')).snapshots[0]

    def test_named_user_mask_default_acl_group_and_directory_traversal(self):
        values = self.snapshot(['/checks/acl', '/checks/acl/file', '/checks/default', '/checks/group', '/checks/no-search', '/checks/link'])
        self.assertEqual([(s.fs_readable, s.fs_writable, s.fs_executable) for s in values.filesystems],
                         [(1, 0, 1), (1, 0, 0), (0, 0, 0), (1, 1, 1), (1, 1, 0), (1, 0, 0)])
        self.assertEqual(self.obj.exec_run(['sh', '-c', 'setfacl -m m::rwx /checks/acl; setfacl -m m::rw- /checks/acl/file']).exit_code, 0)
        values = self.snapshot(['/checks/acl', '/checks/acl/file'])
        self.assertEqual([s.fs_writable for s in values.filesystems], [1, 1])

    def test_missing_path_wrong_supplementary_groups_and_root_are_unknown(self):
        for paths, identity in ((['/checks/missing'], None),
                                (['/checks/group'], dict(uid=1000, gid=1000, groups=[1000, 2000])),
                                (['/checks/group'], dict(uid=0, gid=0, groups=[0]))):
            with self.subTest(paths=paths, identity=identity), self.assertRaises(MonitoringError) as caught:
                self.snapshot(paths, identity)
            self.assertEqual(caught.exception.code, 115)
