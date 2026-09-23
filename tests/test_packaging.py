"""Release checks performed on temporary copies, never on the checkout."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless((ROOT / 'setup.py').is_file(), 'requires a source checkout')
class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.source = self.work / 'source'
        shutil.copytree(str(ROOT), str(self.source), ignore=shutil.ignore_patterns(
            '.git', 'build', 'dist', '*.egg-info', '__pycache__', '.venv'))

    def run_command(self, *command, **kwargs):
        result = subprocess.run(command, cwd=str(kwargs.get('cwd', self.source)),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    @unittest.skipUnless(shutil.which('make') and shutil.which('sed'), 'requires make and sed')
    def test_version_target_updates_package(self):
        self.run_command('make', 'build-git-version', 'PROJECT_VERSION=9.8.7',
                         'PROJECT_RELEASE=9.8.7')
        output = self.run_command(sys.executable, '-c',
                                  'import monit_docker; print(monit_docker.__version__)')
        self.assertEqual(output.strip(), '9.8.7')

    def test_sdist_rebuild_and_installed_entry_points(self):
        self.run_command(sys.executable, 'setup.py', 'sdist')
        archive = next((self.source / 'dist').glob('*.tar.gz'))
        with tarfile.open(str(archive)) as source_archive:
            members = source_archive.getnames()
        for filename in ('setup.yml', 'requirements.txt', 'pyproject.toml'):
            self.assertTrue(any(m.endswith('/' + filename) for m in members), filename)
        self.assertFalse(any('/ui/static/' in name for name in members))
        self.assertFalse(any('/secrets.local/' in name for name in members))
        wheels = self.work / 'wheels'
        self.run_command(sys.executable, '-m', 'pip', 'wheel', '--no-deps',
                         '--no-build-isolation', '--wheel-dir', str(wheels), str(archive))
        wheel = next(wheels.glob('*.whl'))
        installed = self.work / 'installed'
        self.run_command(sys.executable, '-m', 'pip', 'install', '--no-deps',
                         '--target', str(installed), str(wheel))
        # Resolve the installed package, not the source checkout. Keep dependency
        # site-packages available, as pip --no-deps deliberately does not copy them.
        run = ('import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); '
               'entry = sys.argv.pop(1); sys.argv = [entry, "--help"]; '
               'runpy.run_path(entry, run_name="__main__")')
        self.run_command(sys.executable, '-c', run, str(installed),
                         str(installed / 'bin' / 'monit-docker'), cwd=self.work)
        self.run_command(sys.executable, '-c',
                         'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); '
                         'sys.argv = ["monit-docker", "--help"]; '
                         'runpy.run_module("monit_docker", run_name="__main__")',
                         str(installed), cwd=self.work)
        output = self.run_command(sys.executable, '-c',
                         'import sys, json; sys.path.insert(0, sys.argv[1]); '
                         'import monit_docker; from importlib.metadata import version; '
                         'print(json.dumps([monit_docker.__version__, version("monit-docker")]))',
                         str(installed), cwd=self.work)
        internal, metadata = json.loads(output)
        self.assertEqual(internal, metadata)
        self.assertEqual(internal, (self.source / 'VERSION').read_text().strip())
