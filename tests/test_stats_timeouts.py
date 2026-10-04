"""Deterministic socket/watchdog ordering and transport cleanup contracts."""
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests.exceptions import ConnectionError, ConnectTimeout, ReadTimeout
from urllib3.exceptions import ReadTimeoutError

from monit_docker.adapters.stats import read_stats
from monit_docker.domain.errors import MonitoringError


class StatsTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.response = Mock()
        self.api = Mock(timeout=2)
        self.api._get.return_value = self.response
        self.api._url.return_value = 'http://fixture.invalid/stats'
        self.container = SimpleNamespace(client=SimpleNamespace(api=self.api), id='fixture')
        self.timer = Mock()
        self.timer_factory = patch('monit_docker.adapters.stats.Timer', return_value=self.timer).start()
        self.addCleanup(patch.stopall)

    def assert_timeout(self):
        with self.assertRaises(MonitoringError) as error:
            list(read_stats(self.container))
        self.assertEqual(error.exception.code, 115)
        self.assertIn('sampling timed out', str(error.exception))
        self.assertNotIn('transport-secret-marker', str(error.exception))

    def assert_cleaned(self):
        self.timer.cancel.assert_called_once_with()
        self.timer.join.assert_called_once_with()
        self.response.close.assert_called_once_with()

    def test_socket_timeout_before_watchdog_has_stable_code(self):
        errors = (ReadTimeout('transport-secret-marker'), socket.timeout('transport-secret-marker'),
                  ReadTimeoutError(None, None, 'transport-secret-marker'),
                  ConnectionError(ReadTimeoutError(None, None, 'transport-secret-marker')))
        for error in errors:
            with self.subTest(type=type(error).__name__):
                self.timer.reset_mock()
                self.response.reset_mock()
                self.response.iter_content.side_effect = error
                # The mocked timer never fires: the socket definitely wins.
                self.assert_timeout()
                self.assert_cleaned()

    def test_watchdog_before_socket_failure_has_stable_code(self):
        def timer_factory(interval, interrupt):
            self.timer.start.side_effect = interrupt
            return self.timer
        self.timer_factory.side_effect = timer_factory
        self.response.iter_content.side_effect = OSError('socket closed')
        self.assert_timeout()
        self.assert_cleaned()
        self.api._get_raw_response_socket.return_value._sock.socket.shutdown.assert_called_once_with(socket.SHUT_RDWR)

    def test_connection_and_header_timeouts_are_normalized_without_timer(self):
        for error in (ConnectTimeout('transport-secret-marker'), ReadTimeout('transport-secret-marker')):
            with self.subTest(type=type(error).__name__):
                self.api._get.side_effect = error
                self.assert_timeout()
                self.timer_factory.assert_not_called()
                self.response.close.assert_not_called()

    def test_other_transport_errors_are_preserved_without_text_matching(self):
        for error in (ConnectionError('Read timed out'), ConnectionError(),
                      ConnectionError(OSError('connection reset')), ValueError('invalid chunk')):
            with self.subTest(error=repr(error)):
                self.timer.reset_mock()
                self.response.reset_mock()
                self.response.iter_content.side_effect = error
                with self.assertRaises(type(error)) as caught:
                    list(read_stats(self.container))
                self.assertIs(caught.exception, error)
                self.assert_cleaned()

    def test_cleanup_failure_does_not_replace_normalized_timeout(self):
        self.response.iter_content.side_effect = ConnectionError(ReadTimeoutError(None, None, 'timeout'))
        self.response.close.side_effect = RuntimeError('cleanup failure')
        with self.assertLogs('monit-docker', level='ERROR'):
            self.assert_timeout()
        self.assert_cleaned()
