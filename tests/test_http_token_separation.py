import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from monit_docker.adapters.http_security import load_security
from monit_docker.domain.errors import MonitoringError


class TokenSeparationTests(unittest.TestCase):
    def test_roles_require_distinct_secret_values(self):
        with TemporaryDirectory() as directory:
            paths = [Path(directory) / role for role in ('action', 'notification', 'audit', 'metrics', 'status')]
            for values, accepted in [('aabcd', False), ('abacd', False), ('abbcd', False),
                                     ('abcad', False), ('abcbd', False), ('abccd', False),
                                     ('abcda', False), ('abcdb', False), ('abcdc', False),
                                     ('abcdd', False), ('abcde', True)]:
                with self.subTest(values=values):
                    for path, value in zip(paths, values):
                        path.write_text(value * 64)
                    options = dict(action_token_file=paths[0], notification_token_file=paths[1],
                                   audit_read_token_file=paths[2], metrics_token_file=paths[3],
                                   status_token_file=paths[4])
                    if accepted:
                        security = load_security(**options)
                        self.assertEqual(security.notification_token, values[1] * 64)
                    else:
                        with self.assertRaises(MonitoringError):
                            load_security(**options)
