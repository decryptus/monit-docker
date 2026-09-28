"""Regression tests for false-green collection failures."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


GUARD = Path(__file__).resolve().parents[1] / 'scripts' / 'check-test-collection.py'
CASE = 'import unittest\nclass Example(unittest.TestCase):\n    def test_ok(self): pass\n'


class CollectionGuardTests(unittest.TestCase):
    def check(self, files, directories=('tests',), runner='unittest', succeeds=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for directory in directories:
                (root / directory).mkdir(parents=True, exist_ok=True)
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            result = subprocess.run(
                [sys.executable, str(GUARD), '--runner', runner] + list(directories),
                cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, timeout=30)
            self.assertEqual(result.returncode == 0, succeeds, result.stdout)
            return result.stdout

    def test_valid_and_skipped_cases_are_collected(self):
        self.check({'tests/test_valid.py': CASE +
                    '    @unittest.skip("requires integration service")\n'
                    '    def test_skipped(self): pass\n'}, succeeds=True)

    def test_unittest_rejects_ignored_function_next_to_valid_case(self):
        output = self.check({'tests/test_mixed.py': CASE + '\ndef test_lost(): pass\n'})
        self.assertIn('test_lost', output)

    def test_empty_suite_fails(self):
        self.assertIn('Empty test suite', self.check({}))

    def test_nested_directory_requires_separate_discovery(self):
        files = {'tests/test_outer.py': CASE, 'tests/contracts/test_inner.py': CASE}
        self.assertIn('not collected', self.check(files))
        self.check(files, directories=('tests', 'tests/contracts'), succeeds=True)

    def test_import_error_fails(self):
        self.assertIn('ImportError', self.check({
            'tests/test_broken.py': 'raise ImportError("broken dependency")\n'}))

    def test_duplicate_definition_fails(self):
        self.assertIn('Duplicate test declaration', self.check({
            'tests/test_duplicate.py': CASE + '    def test_ok(self): pass\n'}))

    def test_plain_class_is_not_a_unittest_case(self):
        self.assertIn('TestLost.test_lost', self.check({
            'tests/test_plain.py': CASE + '\nclass TestLost:\n    def test_lost(self): pass\n'}))

    @unittest.skipUnless(importlib.util.find_spec('pytest'), 'pytest runner only')
    def test_pytest_parameterization_and_deselection(self):
        files = {'tests/test_parameters.py':
                 'import pytest\n@pytest.mark.parametrize("value", [1, 2])\n'
                 'def test_value(value): assert value > 0\n'}
        self.check(files, runner='pytest', succeeds=True)
        files['tests/conftest.py'] = (
            'def pytest_collection_modifyitems(items):\n    items.clear()\n')
        self.check(files, runner='pytest')
