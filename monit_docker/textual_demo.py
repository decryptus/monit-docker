"""Synthetic, offline fixture for the real Textual adapter and documentation capture."""
from copy import deepcopy
from .textual_tui import MonitorApp

DEMO_SNAPSHOT = dict(
    containers=[dict(id='api', name='api-gateway', status='running', health='healthy',
                     cpu_percent=87, mem_percent=64, disk_percent=42, note='CPU observation; no intervention'),
                dict(id='db', name='postgres', status='running', health='healthy', cpu_percent=24, mem_percent=58),
                dict(id='export', name='exporter', status='running', health='healthy', cpu_percent=11, mem_percent=21)],
    records=[dict(timestamp='2026-10-06T22:50:00Z', container_name='api-gateway', category='monitoring',
                  action='cpu-check', result='warning', message='CPU above configured threshold; synthetic event'),
             dict(timestamp='2026-10-06T22:49:00Z', container_name='postgres', category='monitoring',
                  action='health-check', result='success', message='Synthetic healthy observation')],
    collection_error=None, journal_error=None, updated=1, age=0, stale=False,
    journal_more=False, journal_enabled=True)


class DemoObservation:
    interval = 30

    def __init__(self):
        self.data = deepcopy(DEMO_SNAPSHOT)

    def snapshot(self):
        return deepcopy(self.data)


def demo_app():
    return MonitorApp(DemoObservation(), demo=True)


if __name__ == '__main__':
    demo_app().run()
