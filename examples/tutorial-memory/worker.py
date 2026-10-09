"""Disposable memory demonstration, controlled explicitly with POSIX signals.

SIGUSR1 grows a bounded in-memory cache; SIGUSR2 releases it. Restarting also
returns to the idle state. This simulates a worker retaining too much data; it
does not reproduce a particular application's memory leak.
"""

import json
import signal
import time


CHUNK_BYTES = 12 * 1024 * 1024
MAX_CHUNKS = 30
TICK_SECONDS = 1


def main():
    state = {'grow': False, 'clear': False, 'stop': False}
    blocks = []

    def grow(_signum, _frame):
        state['grow'] = True

    def clear(_signum, _frame):
        state.update(grow=False, clear=True)

    def stop(_signum, _frame):
        state['stop'] = True

    signal.signal(signal.SIGUSR1, grow)
    signal.signal(signal.SIGUSR2, clear)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    ticks = 0
    while not state['stop']:
        if state['clear']:
            blocks.clear()
            state['clear'] = False
        if state['grow'] and len(blocks) < MAX_CHUNKS:
            blocks.append(bytearray(b'x') * CHUNK_BYTES)
        if ticks % 5 == 0:
            print(json.dumps({'tick': ticks, 'cache_mib': len(blocks) * 12,
                              'growing': state['grow']}), flush=True)
        ticks += 1
        time.sleep(TICK_SECONDS)


if __name__ == '__main__':
    main()
