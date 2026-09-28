"""Real curses signal cleanup without Docker or an SSH server."""
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
import unittest

_PROGRAM = '''
from types import SimpleNamespace
from monit_docker.observation import Observation
from monit_docker.domain.models import ContainerSnapshot
from monit_docker.tui import run
raise SystemExit(run(lambda: Observation(lambda: SimpleNamespace(snapshots=[
    ContainerSnapshot(id='a'*64, name='signal-fixture', status='running')]))))
'''


class TerminalSignalTests(unittest.TestCase):
    def test_terminal_restored_on_interrupt_hangup_and_termination(self):
        for signum in (signal.SIGINT, signal.SIGHUP, signal.SIGTERM):
            with self.subTest(signal=signum):
                master, slave = pty.openpty()
                before = termios.tcgetattr(slave)
                fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
                process = subprocess.Popen([sys.executable, '-c', _PROGRAM],
                                           stdin=slave, stdout=slave, stderr=slave,
                                           env=dict(os.environ, TERM='xterm'))
                try:
                    data = bytearray()
                    deadline = time.monotonic() + 5
                    while b'signal-fixture' not in data and time.monotonic() < deadline:
                        if select.select([master], [], [], 0.1)[0]:
                            data.extend(os.read(master, 65536))
                        self.assertLess(len(data), 256 * 1024)
                    self.assertIn(b'signal-fixture', data)
                    self.assertNotEqual(termios.tcgetattr(slave), before)
                    process.send_signal(signum)
                    self.assertEqual(process.wait(timeout=5), 128 + signum)
                    self.assertEqual(termios.tcgetattr(slave), before)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    termios.tcsetattr(slave, termios.TCSANOW, before)
                    os.close(master)
                    os.close(slave)
