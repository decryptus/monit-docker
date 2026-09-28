"""Opt-in real Docker and PTY acceptance; no SSH transport is simulated."""
import errno
import fcntl
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
import uuid

import docker
from monit_docker.audit import AuditJournal

TERM_SIZE = (24, 110)
TIMEOUT = 30
TRANSCRIPT_LIMIT = 256 * 1024


@unittest.skipUnless(os.environ.get('MONIT_DOCKER_INTEGRATION') == '1', 'requires opt-in Docker daemon')
class TerminalDockerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.client = docker.from_env(timeout=15)
        self.addCleanup(self.client.close)
        self.container = self.client.containers.run(
            'alpine:3.20', ['sleep', '120'], name='tui-test-' + uuid.uuid4().hex[:12], detach=True)
        self.addCleanup(self.container.remove, force=True)
        self.container.reload()
        self.started = self.container.attrs['State']['StartedAt']
        self.restarts = self.container.attrs['RestartCount']
        self.journal = self.path / 'events.jsonl'
        AuditJournal(self.journal, emit=False).record(
            'action', 'completed', container_id=self.container.id,
            container_name=self.container.name, action='restart', result='succeeded',
            source='manual', actor='terminal-test', correlation_id=uuid.uuid4().hex)
        self.original = self.journal.read_bytes()
        self.command = [sys.executable, '-m', 'monit_docker', '-c', str(self.path / 'absent.yml'),
                        '--client-from-env', '--id', self.container.id,
                        '--audit-file', str(self.journal)]
        self.environment = dict(os.environ, TERM='xterm-256color')
        self.environment.pop('MONIT_DOCKER_CONFIG', None)

    def unchanged(self):
        self.container.reload()
        self.assertEqual(self.container.status, 'running')
        self.assertEqual(self.container.attrs['State']['StartedAt'], self.started)
        self.assertEqual(self.container.attrs['RestartCount'], self.restarts)
        self.assertEqual(self.journal.read_bytes(), self.original)

    def test_real_terminal_navigation_resize_and_read_only_exit(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        before = termios.tcgetattr(slave)
        def resize(height, width):
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', height, width, 0, 0))
        resize(*TERM_SIZE)
        process = subprocess.Popen(self.command + ['tui', '--refresh', '5'],
                                   stdin=slave, stdout=slave, stderr=slave, env=self.environment)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        self.addCleanup(cleanup)
        def expect(needle):
            data = bytearray()
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.2)[0]:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError as error:
                        if error.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    data.extend(chunk)
                    self.assertLessEqual(len(data), TRANSCRIPT_LIMIT)
                    if needle.encode() in data:
                        return
                if process.poll() is not None:
                    break
            self.fail('Terminal did not display %r; exit=%r; tail=%r' %
                      (needle, process.poll(), bytes(data[-2000:])))
        expect(self.container.name)
        os.write(master, b'\r')
        expect('Read-only snapshot')
        os.write(master, b'q')
        expect('Containers')
        resize(6, 32)
        process.send_signal(signal.SIGWINCH)
        expect('Terminal too small')
        resize(*TERM_SIZE)
        process.send_signal(signal.SIGWINCH)
        expect(self.container.name)
        os.write(master, b'\t')
        expect('succeeded')
        os.write(master, b'q')
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertEqual(termios.tcgetattr(slave), before, 'curses did not restore terminal attributes')
        self.unchanged()

    def test_noninteractive_tui_refusal_and_cron_dry_run(self):
        rejected = subprocess.run(self.command + ['tui'], input='', capture_output=True,
                                  text=True, env=self.environment, timeout=10)
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        self.assertIn('interactive terminal', rejected.stderr)
        self.assertNotIn('\x1b[', rejected.stdout + rejected.stderr)
        self.unchanged()
        state = self.path / 'cron.json'
        cron = subprocess.run(self.command + ['cron', '--state-file', str(state),
                                             '--cmd', 'restart', '--dry-run'],
                              input='', capture_output=True, text=True, env=self.environment, timeout=TIMEOUT)
        self.assertEqual(cron.returncode, 0, cron.stderr)
        self.assertIn('dry-run', cron.stdout + cron.stderr)
        self.assertNotIn('\x1b[', cron.stdout + cron.stderr)
        self.container.reload()
        self.assertEqual(self.container.attrs['State']['StartedAt'], self.started)
        self.assertEqual(self.container.attrs['RestartCount'], self.restarts)
