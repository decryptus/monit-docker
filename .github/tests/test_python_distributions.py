import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check-python-distributions.py'
SPEC = importlib.util.spec_from_file_location('distributions', SCRIPT)
distributions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(distributions)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'dist').mkdir()
        (self.root / 'monit_docker').mkdir()
        (self.root / 'VERSION').write_text('1.2.3\n')
        (self.root / 'RELEASE').write_text('1.2.3\n')
        (self.root / 'monit_docker/__init__.py').write_text("__version__ = '1.2.3'\n")
        self.write_archives()

    def write_archives(self, wheel_version='1.2.3', source_version='1.2.3', name='monit_docker'):
        with zipfile.ZipFile(self.root / 'dist/monit_docker-1.2.3-py3-none-any.whl', 'w') as wheel:
            wheel.writestr('monit_docker-1.2.3.dist-info/METADATA',
                          'Metadata-Version: 2.1\nName: %s\nVersion: %s\n' % (name, wheel_version))
        data = ('Metadata-Version: 2.1\nName: %s\nVersion: %s\n' % (name, source_version)).encode()
        with tarfile.open(self.root / 'dist/monit_docker-1.2.3.tar.gz', 'w:gz') as source:
            info = tarfile.TarInfo('monit_docker-1.2.3/PKG-INFO')
            info.size = len(data)
            source.addfile(info, io.BytesIO(data))

    def test_valid_distributions_accept_normalized_project_name(self):
        self.assertEqual(distributions.check_distributions(self.root), '1.2.3')

    def test_rejects_inconsistent_release_and_module_versions(self):
        for path in ('RELEASE', 'monit_docker/__init__.py'):
            with self.subTest(path=path):
                target = self.root / path
                original = target.read_text()
                target.write_text(original.replace('1.2.3', '1.2.2'))
                with self.assertRaisesRegex(ValueError, 'must match VERSION'):
                    distributions.check_distributions(self.root)
                target.write_text(original)

    def test_rejects_prerelease_version(self):
        (self.root / 'VERSION').write_text('1.2.3rc1')
        with self.assertRaisesRegex(ValueError, 'VERSION must be X.Y.Z'):
            distributions.check_distributions(self.root)

    def test_rejects_mismatched_archive_versions(self):
        for kwargs in ({'wheel_version': '1.2.2'}, {'source_version': '1.2.2'}):
            with self.subTest(kwargs=kwargs):
                self.write_archives(**kwargs)
                with self.assertRaisesRegex(ValueError, 'distribution name/version'):
                    distributions.check_distributions(self.root)

    def test_rejects_wrong_project(self):
        self.write_archives(name='another-project')
        with self.assertRaisesRegex(ValueError, 'distribution name/version'):
            distributions.check_distributions(self.root)

    def test_rejects_missing_source_archive(self):
        (self.root / 'dist/monit_docker-1.2.3.tar.gz').unlink()
        with self.assertRaisesRegex(ValueError, 'exactly one wheel and one source'):
            distributions.check_distributions(self.root)

    def test_rejects_extra_artifacts(self):
        (self.root / 'dist/old-release.whl').touch()
        with self.assertRaisesRegex(ValueError, 'exactly one wheel and one source'):
            distributions.check_distributions(self.root)

    def test_rejects_missing_wheel_metadata(self):
        with zipfile.ZipFile(self.root / 'dist/monit_docker-1.2.3-py3-none-any.whl', 'w'):
            pass
        with self.assertRaisesRegex(ValueError, 'exactly one METADATA'):
            distributions.check_distributions(self.root)

    def test_rejects_private_notification_files_in_published_archives(self):
        private_paths = ['examples/monitoring/notifications.local/alertmanager.yml',
                         'examples/monitoring/notifications.local/smtp_password',
                         'examples/monitoring/notifications.local/slack_webhook_url',
                         'examples/monitoring/.env']
        for path in private_paths:
            for kind in ('wheel', 'source'):
                with self.subTest(path=path, kind=kind):
                    self.write_archives()
                    if kind == 'wheel':
                        with zipfile.ZipFile(self.root / 'dist/monit_docker-1.2.3-py3-none-any.whl', 'a') as archive:
                            archive.writestr(path, 'private fixture')
                    else:
                        source = self.root / 'dist/monit_docker-1.2.3.tar.gz'
                        with tarfile.open(source, 'r:gz') as archive:
                            metadata = archive.extractfile('monit_docker-1.2.3/PKG-INFO').read()
                        with tarfile.open(source, 'w:gz') as archive:
                            for name, data in [('PKG-INFO', metadata), (path, b'private fixture')]:
                                info = tarfile.TarInfo('monit_docker-1.2.3/' + name)
                                info.size = len(data)
                                archive.addfile(info, io.BytesIO(data))
                    with self.assertRaisesRegex(ValueError, 'private monitoring configuration'):
                        distributions.check_distributions(self.root)


if __name__ == '__main__':
    unittest.main()
