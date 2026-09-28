"""Services are transport-independent; credentials belong to each HTTP server."""
import ast
import unittest
import tempfile
import monit_docker
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from threading import Thread
from unittest.mock import Mock

from monit_docker.adapters.http import StatusServer
from monit_docker.adapters.http_security import HttpSecurity
from monit_docker.audit import AuditJournal
from monit_docker.audit_query import AuditReader
from monit_docker.manual_actions import ManualActions
from monit_docker.notification_audit import NotificationAudit
from monit_docker.service import MonitorService

PACKAGE_ROOT = Path(monit_docker.__file__).resolve().parent
ROOT = PACKAGE_ROOT.parent
SERVICES = ('manual_actions.py', 'notification_audit.py', 'audit_query.py')
FORBIDDEN_IMPORTS = ('httpdis', 'urllib', 'argparse', 'curses',
                     'monit_docker.cli', 'monit_docker.adapters')


@contextmanager
def serving(monitor, security=None):
    server = StatusServer(('127.0.0.1', 0), monitor, security)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(3)
        server.server_close()
        assert not thread.is_alive()


def request(server, path, method='GET', headers=None, payload=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    try:
        connection.request(method, path, json.dumps(payload) if payload is not None else None, headers or {})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


class HttpServiceBoundaryTests(unittest.TestCase):
    def test_services_have_no_transport_imports_including_lazy_imports(self):
        for filename in SERVICES:
            for node in ast.walk(ast.parse((PACKAGE_ROOT / filename).read_text())):
                names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else (
                    [node.module or ''] if isinstance(node, ast.ImportFrom) else [])
                assert not any(name == blocked or name.startswith(blocked + '.')
                               for name in names for blocked in FORBIDDEN_IMPORTS), (filename, node.lineno)


    def test_services_execute_without_http_cli_or_secrets(self):
        program = textwrap.dedent('''
            import importlib.abc
            import sys
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from unittest.mock import Mock
            class BlockInterfaces(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if any(fullname == prefix or fullname.startswith(prefix + '.') for prefix in
                           ('httpdis', 'monit_docker.adapters', 'monit_docker.cli', 'curses')):
                        raise AssertionError('Interface imported: ' + fullname)
            sys.meta_path.insert(0, BlockInterfaces())
            from monit_docker.audit import AuditJournal
            from monit_docker.audit_query import AuditQuery, AuditReader, QueryError
            from monit_docker.manual_actions import ManualActions
            from monit_docker.notification_audit import NotificationAudit
            from monit_docker.domain.errors import ActionRejected
            with TemporaryDirectory() as tmp:
                journal = AuditJournal(Path(tmp) / 'events.jsonl', emit=False)
                actions = ManualActions(Mock(), audit=journal)
                receiver = NotificationAudit(journal)
                reader = AuditReader(journal)
                for service in (actions, receiver, reader):
                    assert not any(hasattr(service, name) for name in ('token', 'origin', 'trust_actor'))
                payload = dict(request_id='a' * 32, container_id='b' * 64, action='restart')
                status = dict(ready=True, containers=[dict(id='b' * 64, name='web', status='running')])
                actions.submit(payload, status, actor='alice')
                try:
                    actions.submit(payload, status, actor='bob')
                    raise AssertionError('Request ownership bypassed')
                except ActionRejected as error:
                    assert error.reason == 'request_id_conflict'
                record = actions.take()
                actions.execute(record['container_id'], record['action'])
                actions.complete(record['request_id'])
                actions.execute.assert_called_once_with('b' * 64, 'restart')
                assert actions.status()['recent'][0]['status'] == 'succeeded'
                receiver.receive(dict(version='4', receiver='ops', groupKey='group', status='firing',
                                      alerts=[dict(status='firing', labels={'alertname': 'Down'})]))
                query = AuditQuery({'category': 'notification'})
                page = reader.page(query, 'alice')
                assert len(page['records']) == 1 and page['records'][0]['actor'] == 'alertmanager'
                replay = AuditQuery(query.filters, page['page_cursor'])
                assert reader.page(replay, 'alice')['records'] == page['records']
                try:
                    reader.page(replay, 'bob')
                    raise AssertionError('Cursor identity bypassed')
                except QueryError as error:
                    assert error.reason == 'invalid_cursor' and not hasattr(error, 'code')
                for invalid in ('category=notification', AuditQuery({'category': 'invalid'}),
                                AuditQuery({'format': 'csv'}), AuditQuery({'container': 1}),
                                AuditQuery({}, 123)):
                    try:
                        reader.page(invalid, 'alice')
                        raise AssertionError('Invalid direct request accepted')
                    except QueryError:
                        pass
        ''')
        result = subprocess.run([sys.executable, '-c', program], cwd=str(ROOT),
                                env=dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', '')),
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr


    def test_secrets_are_scoped_to_server_even_with_a_shared_reader(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tmp_path = Path(directory.name)
        journal = AuditJournal(tmp_path / 'events.jsonl', emit=False)
        reader = AuditReader(journal)
        monitor = MonitorService(Mock(), audit_reader=reader)
        alpha, beta = HttpSecurity(audit_token='a' * 64), HttpSecurity(audit_token='b' * 64)
        assert 'a' * 64 not in repr(alpha)
        with serving(monitor, alpha) as first, serving(monitor, beta) as second:
            for server, valid, invalid in ((first, 'a' * 64, 'b' * 64), (second, 'b' * 64, 'a' * 64)):
                assert request(server, '/v1/audit', headers={'X-Monit-Audit-Token': valid, 'X-Monit-Actor': 'alice'})[0] == 200
                assert request(server, '/v1/audit', headers={'X-Monit-Audit-Token': invalid, 'X-Monit-Actor': 'alice'}) == (403, {'error': 'forbidden'})


    def test_services_without_transport_credentials_do_not_enable_private_access(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        tmp_path = Path(directory.name)
        journal = AuditJournal(tmp_path / 'events.jsonl', emit=False)
        actions = ManualActions(Mock(), audit=journal)
        monitor = MonitorService(Mock(), manual_actions=actions,
                                 notification_audit=NotificationAudit(journal), audit_reader=AuditReader(journal))
        headers = {'Content-Type': 'application/json', 'Origin': 'https://monitor.test',
                   'X-Monit-Action-Token': 'a' * 64, 'X-Monit-Audit-Token': 'a' * 64,
                   'X-Monit-Actor': 'alice', 'Authorization': 'Bearer ' + 'a' * 64}
        with serving(monitor) as server:
            for path, method in (('/v1/actions', 'POST'), ('/v1/notifications', 'POST'), ('/v1/audit', 'GET')):
                assert request(server, path, method, headers, {} if method == 'POST' else None) == (403, {'error': 'forbidden'})
        assert actions.status()['recent'] == []
        assert journal.read() == []
