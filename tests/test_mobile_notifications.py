import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock
from monit_docker.adapters.local_auth import Accounts
from monit_docker.adapters.mobile_push import PushDevices, ExpoSender
from monit_docker.mobile_notifications import MobileNotifications, observation_episode
from test_local_auth import FastPasswords, PASSWORD

DEVICE = {'token': 'ExponentPushToken[fixture-token-12345]', 'language': 'fr', 'channel': 'monit-observations-1-1'}

class MobileNotificationsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.now = [1000]
        self.accounts = Accounts(Path(directory.name) / 'auth.db', create=True, passwords=FastPasswords(), clock=lambda: self.now[0])
        self.addCleanup(self.accounts.close)
        self.accounts.provision('alice', PASSWORD, 'viewer')
        self.grant = self.accounts.login('alice', PASSWORD, 'fixture')
        self.devices = PushDevices(self.accounts)
        self.devices.register('alice', self.grant.secret, DEVICE)
        self.sender = Mock()
        self.sender.send.return_value = {'status': 'ok', 'id': 'receipt-fixture'}
        self.snapshot = {'ready': True, 'containers': [{'id': 'a'*64, 'status': 'exited', 'health': 'none'}]}
        self.delivery = MobileNotifications(self.devices, self.sender, lambda: self.snapshot, 'https://monitor.example', clock=lambda: self.now[0])

    def test_changed_episode_deduplication_and_generic_delivery(self):
        self.delivery.cycle(); self.delivery.cycle()
        self.assertEqual(self.sender.send.call_count, 1)
        self.assertEqual(self.devices.pending()[0][2]['delivery'], 'accepted')
        self.snapshot['containers'] = []
        self.now[0] += 901
        self.sender.receipt.return_value = {'status': 'ok'}
        self.delivery.cycle(); self.delivery.cycle()
        self.assertIsNone(self.devices.pending()[0][2]['episode'])
        self.snapshot['ready'] = False
        self.delivery.cycle()
        self.assertEqual(self.sender.send.call_count, 2)

    def test_receipt_failure_removes_unregistered_device(self):
        self.delivery.cycle()
        self.now[0] += 901
        self.sender.receipt.return_value = {'status': 'error', 'details': {'error': 'DeviceNotRegistered'}}
        self.delivery.cycle()
        self.assertEqual(self.devices.pending(), [])

    def test_network_failure_is_backed_off_across_worker_restart(self):
        self.sender.send.side_effect = OSError('fixture')
        with self.assertLogs('monit-docker', level='WARNING') as output:
            self.delivery.cycle()
        self.assertNotIn(DEVICE['token'], str(output.output))
        other = MobileNotifications(self.devices, self.sender, lambda: self.snapshot, 'https://monitor.example', clock=lambda: self.now[0])
        other.cycle()
        self.assertEqual(self.sender.send.call_count, 1)

    def test_expiry_role_change_and_revocation_stop_delivery(self):
        self.accounts.set_role('alice', 'metrics')
        self.assertEqual(self.devices.pending(), [])
        self.accounts.set_role('alice', 'viewer')
        grant = self.accounts.login('alice', PASSWORD, 'fixture')
        self.devices.register('alice', grant.secret, DEVICE)
        self.devices.revoke_session('alice', self.accounts.sessions('alice')[0]['id'])
        self.assertEqual(self.devices.pending(), [])
        self.devices.register('alice', grant.secret, DEVICE)
        self.now[0] += 7 * 86400 + 1
        self.assertEqual(self.devices.pending(), [])

    def test_device_moves_between_accounts_and_cannot_replay_old_update(self):
        before = self.devices.pending()[0][2]
        self.accounts.provision('bob', PASSWORD, 'viewer')
        grant = self.accounts.login('bob', PASSWORD, 'fixture')
        self.devices.register('bob', grant.secret, DEVICE)
        self.assertEqual([row[0] for row in self.devices.pending()], ['bob'])
        self.assertFalse(self.devices.update('alice', 'wrong', before, delivery='accepted'))

    def test_device_validation_and_capacity(self):
        for patch in ({'token': 'https://evil.example'}, {'language': 'xx'}, {'channel': '../secret'}):
            with self.assertRaises(ValueError):
                self.devices.register('alice', self.grant.secret, dict(DEVICE, **patch))
        for index in range(4):
            self.devices.register('alice', self.grant.secret, dict(DEVICE, token='ExpoPushToken[fixture-token-%d]' % index))
        with self.assertRaises(ValueError):
            self.devices.register('alice', self.grant.secret, dict(DEVICE, token='ExpoPushToken[fixture-token-limit]'))

    def test_maintenance_and_missing_data_are_not_healthy(self):
        self.assertEqual(observation_episode({'ready': False}), 'unavailable')
        self.assertIsNone(observation_episode({'ready': True, 'containers': [{'id': 'a', 'status': 'exited', 'maintenance_active': True}]}))

    def test_expo_payload_hides_workload_details_and_rejects_redirects(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.iter_content.return_value = [b'{"data":{"status":"ok","id":"fixture"}}']
        request = Mock(return_value=response)
        sender = ExpoSender(request)
        sender.send(DEVICE, 'https://monitor.example')
        args = request.call_args.kwargs
        self.assertFalse(args['allow_redirects'])
        self.assertEqual(args['json']['data'], {'screen': 'alerts', 'origin': 'https://monitor.example'})
        self.assertNotIn('container', str(args['json']))
        response.status_code = 302
        with self.assertRaises(ValueError):
            sender.send(DEVICE, 'https://monitor.example')
