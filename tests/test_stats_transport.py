"""Actual SDK/HTTP reads: silence, trickle, limits, cleanup and recovery."""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import docker

from monit_docker.adapters.docker import DockerCollector
from monit_docker.adapters.stats import read_stats
from monit_docker.domain.errors import MonitoringError
from monit_docker.domain.models import ContainerSnapshot


class StatsTransportTests(unittest.TestCase):
    def setUp(self):
        owner = self
        self.mode = 'normal'
        self.stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def do_GET(self):
                self.close_connection = True
                self.send_response(200)
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                def send(data):
                    self.wfile.write(('%x\r\n' % len(data)).encode() + data + b'\r\n')
                    self.wfile.flush()
                try:
                    if owner.mode == 'silent':
                        owner.stop.wait(3)
                    elif owner.mode == 'trickle':
                        while not owner.stop.wait(0.01):
                            send(b'{')
                    elif owner.mode == 'oversize':
                        send(b'x' * 4096)
                    else:
                        count = 10 if owner.mode == 'repeated' else 2
                        for index in range(count):
                            sample = {'read': 0 if owner.mode == 'repeated' else index,
                                      'memory_stats': {'usage': 80, 'limit': 100}}
                            send(json.dumps(sample).encode() + b'\n')
                    self.wfile.write(b'0\r\n\r\n')
                except (OSError, ValueError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = docker.DockerClient(base_url='http://127.0.0.1:%s' % self.server.server_port,
                                          version='1.41', timeout=2)
        self.container = SimpleNamespace(client=self.client, id='fixture')
        self.collector = DockerCollector(lambda: self.client)
        self.collector._containers['fixture'] = self.container
        self.snapshot = ContainerSnapshot(id='fixture', name='fixture', status='running')
        self.addCleanup(self.close)

    def close(self):
        self.stop.set()
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def collect(self):
        return self.collector._collect_stats(self.snapshot, ('mem_percent',))

    def test_silent_and_trickling_bodies_are_interrupted_and_recover(self):
        for mode in ('silent', 'trickle'):
            with self.subTest(mode=mode), patch('monit_docker.adapters.stats.STATS_TIMEOUT', 0.15):
                self.mode = mode
                before = time.monotonic()
                with self.assertRaises(MonitoringError) as error:
                    self.collect()
                self.assertEqual(error.exception.code, 115)
                self.assertLess(time.monotonic() - before, 2)
                self.assertFalse(any(isinstance(t, threading.Timer) for t in threading.enumerate()))
                self.mode = 'normal'
                self.assertEqual(self.collect().mem_percent, 80)

    def test_repeated_timestamps_stop_before_consuming_the_whole_stream(self):
        self.mode = 'repeated'
        with self.assertRaises(MonitoringError) as error:
            self.collect()
        self.assertEqual(error.exception.code, 115)
        self.assertIn('progress', str(error.exception))

    def test_oversized_and_partial_responses_release_watchdog(self):
        self.mode = 'oversize'
        with patch('monit_docker.adapters.stats.MAX_STATS_BYTES', 2048):
            with self.assertRaises(MonitoringError):
                list(read_stats(self.container))
        self.mode = 'normal'
        stream = read_stats(self.container)
        next(stream)
        stream.close()
        self.assertFalse(any(isinstance(t, threading.Timer) for t in threading.enumerate()))
