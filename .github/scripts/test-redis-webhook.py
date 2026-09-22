"""Contract tests for the optional receiver; run with its requirements installed."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

source = Path('/app/bridge.py') if __file__ == '<stdin>' else (
    Path(__file__).resolve().parents[2] / 'examples/monitoring/redis-webhook/bridge.py')
spec = importlib.util.spec_from_file_location('bridge', source)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def payload():
    return {'version': '4', 'status': 'firing', 'groupKey': '{}:{job="test"}',
            'alerts': [{'status': 'firing', 'labels': {'alertname': 'Test'}, 'annotations': {}}]}


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.receiver = bridge.RedisWebhook('redis://localhost:6379/0', 'a' * 64)
        self.receiver.notifier = Mock()
        self.request = SimpleNamespace(headers={'Content-Type': 'application/json'}, payload_params=payload)

    def test_full_group_is_acknowledged_after_strict_send(self):
        self.receiver.notifier.send.return_value = {'notifier': b'123-0'}
        result = self.receiver.receive(self.request)
        self.assertEqual(result.code, 202)
        self.assertEqual(json.loads(result.data)['id'], '123-0')
        self.assertEqual(self.receiver.notifier.send.call_args.args[2]['value'], payload())

    def test_failed_delivery_never_acknowledged(self):
        for error in (ConnectionError('down'), TimeoutError('late'), RuntimeError('no ack')):
            with self.subTest(error=type(error)):
                self.receiver.notifier.send.side_effect = error
                self.assertEqual(self.receiver.receive(self.request).code, 503)

    def test_invalid_or_truncated_group_is_never_written(self):
        for value in (None, [], {}, dict(payload(), truncatedAlerts=1), dict(payload(), alerts=[]),
                      dict(payload(), version='5'), dict(payload(), status='unknown')):
            with self.subTest(value=value):
                self.request.payload_params = lambda: value
                self.assertEqual(self.receiver.receive(self.request).code, 400)
        self.receiver.notifier.send.assert_not_called()

    def test_authentication(self):
        self.assertTrue(self.receiver.authorized('Bearer ' + 'a' * 64))
        for header in (None, '', 'a' * 64, 'Bearer ' + 'b' * 64):
            self.assertFalse(self.receiver.authorized(header))

    def test_bounded_timeouts_and_retention(self):
        for suffix in ('socket_timeout=0', 'socket_timeout=nan', 'socket_connect_timeout=6'):
            with self.assertRaises(ValueError):
                bridge.redis_config('redis://localhost/0?' + suffix)
        for maxlen in (0, -1, True, 2**63):
            with self.assertRaises(ValueError):
                bridge.RedisWebhook('redis://localhost', 'a' * 64, maxlen=maxlen)


if __name__ == '__main__':
    unittest.main()
