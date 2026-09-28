#!/usr/bin/env python3
"""Capture the real curses renderer in a PTY with synthetic data (no Docker).

Documentation-only dependencies: pip install pyte pillow
Run from the repository root. Produces PNGs and asserts real keyboard navigation.
"""
import codecs
import fcntl
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import termios
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
WIDTH, HEIGHT = 108, 18
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf'
OUTPUT = ROOT / 'docs' / 'images'


def demo():
    from monit_docker.tui import run
    class Demonstration:
        interval = 30
        def start(self): pass
        def close(self): pass
        def snapshot(self):
            return dict(updated=1, age=0, stale=False, collection_error=None,
                        journal_error=None, journal_enabled=True, journal_more=True,
                        containers=[dict(name=name, status=status, health=health,
                                         cpu_percent=cpu, mem_percent=mem,
                                         id=str(index) * 64)
                                    for index, (name, status, health, cpu, mem) in enumerate([
                                        ('demo-web', 'running', 'healthy', 12.4, 28.1),
                                        ('demo-worker', 'running', 'healthy', 63.2, 51.7),
                                        ('demo-cache', 'running', 'unhealthy', 2.1, 82.3),
                                        ('demo-backup', 'exited', 'none', None, None)], 1)],
                        records=[dict(timestamp='2026-09-28T08:00:00Z', container_name=name,
                                      action=action, result=result, source='automatic')
                                 for name, action, result in [
                                     ('demo-web', 'restart', 'succeeded'),
                                     ('demo-cache', 'restart', 'rejected'),
                                     ('demo-worker', 'notification', 'accepted')]])
    return run(Demonstration)


def capture():
    import pyte
    from PIL import Image, ImageDraw, ImageFont
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', HEIGHT, WIDTH, 0, 0))
    process = subprocess.Popen([sys.executable, __file__, '--demo'], cwd=ROOT,
                               stdin=slave, stdout=slave, stderr=slave,
                               env=dict(os.environ, TERM='xterm-256color'))
    os.close(slave)
    terminal = pyte.Screen(WIDTH, HEIGHT)
    stream = pyte.Stream(terminal)
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    def wait_for(text):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                stream.feed(decoder.decode(os.read(master, 65536)))
            if text in '\n'.join(terminal.display):
                # A curses refresh can arrive in several PTY chunks. Drain the frame.
                while select.select([master], [], [], 0.1)[0]:
                    stream.feed(decoder.decode(os.read(master, 65536)))
                return
        raise AssertionError('Terminal did not render: ' + text)
    def save(name):
        font = ImageFont.truetype(FONT, 16)
        cw, ch, pad = 10, 22, 20
        image = Image.new('RGB', (WIDTH * cw + 2 * pad, HEIGHT * ch + 2 * pad), '#101820')
        draw = ImageDraw.Draw(image)
        for y in range(HEIGHT):
            for x in range(WIDTH):
                char = terminal.buffer[y][x]
                fg, bg = ('#101820', '#dce7ed') if char.reverse else ('#dce7ed', '#101820')
                draw.rectangle((pad + x*cw, pad + y*ch, pad + (x+1)*cw, pad + (y+1)*ch), fill=bg)
                draw.text((pad + x*cw, pad + y*ch), char.data, font=font, fill=fg)
        OUTPUT.mkdir(exist_ok=True)
        image.save(OUTPUT / name)
    try:
        wait_for('demo-web')
        save('terminal-containers.png')
        os.write(master, b'\t')
        wait_for('Journal')
        wait_for('succeeded')
        save('terminal-journal.png')
        os.write(master, b'\r')
        wait_for('Read-only snapshot')
        save('terminal-details.png')
        os.write(master, b'q')
        wait_for('Journal')
        os.write(master, b'q')
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


if __name__ == '__main__':
    if '--demo' in sys.argv:
        raise SystemExit(demo())
    capture()
