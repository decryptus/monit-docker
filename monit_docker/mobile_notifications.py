"""Independent background delivery; never executes actions or pauses monitoring."""
import hashlib
import json
import logging
import time

LOG = logging.getLogger('monit-docker')
POLL_SECONDS = 30
RETRY_SECONDS = 300
RECEIPT_SECONDS = 900
MAX_RECEIPT_AGE = 86400


def observation_episode(snapshot):
    if not snapshot.get('ready'):
        return 'unavailable'
    signals = sorted((row['id'], row.get('status'), row.get('health'))
                     for row in snapshot.get('containers', [])
                     if not row.get('maintenance_active') and
                     (row.get('health') == 'unhealthy' or row.get('status') in ('dead', 'exited', 'restarting')))
    return hashlib.sha256(json.dumps(signals).encode()).hexdigest() if signals else None


class MobileNotifications:
    def __init__(self, devices, sender, snapshot, origin, clock=time.time):
        self.devices, self.sender, self.snapshot, self.origin, self.clock = devices, sender, snapshot, origin, clock

    def cycle(self, stop=None):
        now, episode = self.clock(), observation_episode(self.snapshot())
        for principal, key, device in self.devices.pending():
            if stop is not None and stop.is_set():
                return
            try:
                ticket = device.get('ticket')
                if ticket and now >= device.get('receipt_at', 0):
                    receipt = self.sender.receipt(ticket)
                    if receipt and receipt.get('details', {}).get('error') == 'DeviceNotRegistered':
                        self.devices.remove(principal, key, device)
                        continue
                    if receipt:
                        self.devices.update(principal, key, device, ticket=None,
                                            delivery='delivered' if receipt.get('status') == 'ok' else 'failed')
                    elif now - device['receipt_at'] > MAX_RECEIPT_AGE:
                        self.devices.update(principal, key, device, ticket=None, delivery='unknown')
                    continue
                if episode is None:
                    if device.get('episode') is not None:
                        self.devices.update(principal, key, device, episode=None)
                    continue
                if ticket or episode == device.get('episode') or now < device.get('next_attempt', 0):
                    continue
                # Claim before sending, preserving backoff across restarts. No replay burst.
                changes = {'next_attempt': now + RETRY_SECONDS}
                if not self.devices.update(principal, key, device, **changes):
                    continue
                claimed = dict(device, **changes)
                result = self.sender.send(claimed, self.origin)
                if result.get('details', {}).get('error') == 'DeviceNotRegistered':
                    self.devices.remove(principal, key, claimed)
                elif result.get('status') == 'ok' and isinstance(result.get('id'), str) and len(result['id']) <= 128:
                    self.devices.update(principal, key, claimed, episode=episode, ticket=result['id'],
                                        receipt_at=now + RECEIPT_SECONDS, delivery='accepted')
                else:
                    self.devices.update(principal, key, claimed, delivery='failed')
            except Exception:
                # No tokens, provider bodies or installation data in logs.
                LOG.warning('Mobile push delivery unavailable')

    def run(self, stop):
        while not stop.is_set():
            try:
                self.cycle(stop)
            except Exception:
                LOG.warning('Mobile push registration storage unavailable')
            stop.wait(POLL_SECONDS)
