import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monit_docker.adapters.http_security import load_security
from monit_docker.domain.errors import MonitoringError


class TokenSeparationTests(unittest.TestCase):
    def test_roles_require_distinct_secret_values(self):
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / role for role in ('action', 'notification', 'audit', 'metrics')]
            for values, accepted in [('aabc', False), ('abac', False), ('abbc', False),
                                     ('abca', False), ('abcb', False), ('abcc', False), ('abcd', True)]:
                with self.subTest(values=values):
                    for path, value in zip(paths, values):
                        path.write_text(value * 64)
                    options = dict(action_token_file=paths[0], notification_token_file=paths[1],
                                   audit_read_token_file=paths[2], metrics_token_file=paths[3])
                    if accepted:
                        security = load_security(**options)
                        self.assertEqual(security.notification_token, values[1] * 64)
                    else:
                        with self.assertRaises(MonitoringError):
                            load_security(**options)
