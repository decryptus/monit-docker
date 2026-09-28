#!/usr/bin/env python3
"""Compare test declarations with actual collection (Python 3.8+ CI helper).

Run before the normal test command, with the same interpreter and environment.
Pass every separately discovered directory together; skipped tests still count
as collected. This does not execute tests or replace the normal test command.
"""
import argparse
import ast
import inspect
from pathlib import Path
import sys
import unittest


TEST_PATTERN = 'test*.py'
FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def declarations(directories):
    expected = set()
    files = {path.resolve() for directory in directories
             for path in directory.rglob(TEST_PATTERN)}
    for path in sorted(files):
        tree = ast.parse(path.read_bytes(), filename=str(path))

        def visit(nodes, prefix=''):
            for node in nodes:
                if isinstance(node, FUNCTION_NODES):
                    if node.name.startswith('test'):
                        key = (path, prefix + node.name)
                        if key in expected:
                            raise ValueError('Duplicate test declaration: %s:%s' % key)
                        expected.add(key)
                    # Helpers defined inside a test are not separate tests.
                elif isinstance(node, ast.ClassDef):
                    visit(node.body, prefix + node.name + '.')
                else:
                    # Include declarations behind module/class-level conditions.
                    visit(list(ast.iter_child_nodes(node)), prefix)

        visit(tree.body)
    return expected


def identity(obj):
    obj = inspect.unwrap(obj)
    source = inspect.getsourcefile(obj)
    return (Path(source).resolve(), obj.__qualname__) if source else None


def unittest_collection(directories):
    collected = set()
    count = 0

    def cases(suite):
        for item in suite:
            if isinstance(item, unittest.TestSuite):
                yield from cases(item)
            else:
                yield item

    for directory in directories:
        loader = unittest.TestLoader()
        suite = loader.discover(str(directory), pattern=TEST_PATTERN)
        if loader.errors:
            raise ValueError('\n'.join(loader.errors))
        items = list(cases(suite))
        if not items:
            raise ValueError('Empty test suite: %s' % directory)
        count += len(items)
        for case in items:
            method = getattr(case, case._testMethodName)
            collected.add(identity(method))
    return collected, count


def pytest_collection(directories):
    import pytest

    class Collector:
        def __init__(self):
            self.collected = set()
            self.counts = {directory: 0 for directory in directories}
            self.count = 0

        def pytest_collection_finish(self, session):
            self.count = len(session.items)
            for item in session.items:
                self.collected.add(identity(item.obj))
                for directory in directories:
                    if directory in Path(str(item.path)).resolve().parents:
                        self.counts[directory] += 1

    collector = Collector()
    status = pytest.main(['--collect-only', '-q'] +
                         [str(directory) for directory in directories],
                         plugins=[collector])
    if status != 0:
        raise ValueError('pytest collection failed with exit code %s' % status)
    for directory, count in collector.counts.items():
        if not count:
            raise ValueError('Empty test suite: %s' % directory)
    return collector.collected, collector.count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner', choices=('unittest', 'pytest'), required=True)
    parser.add_argument('directories', type=Path, nargs='+')
    args = parser.parse_args()
    # Match python -m unittest/pytest: use the working directory, never the
    # script repository. Installed-package jobs run from outside the checkout.
    sys.path.insert(0, str(Path.cwd()))
    directories = [directory.resolve() for directory in args.directories]
    try:
        for directory in directories:
            if not directory.is_dir():
                raise ValueError('Missing test directory: %s' % directory)
        expected = declarations(directories)
        collect = pytest_collection if args.runner == 'pytest' else unittest_collection
        collected, count = collect(directories)
        missing = expected - collected
        if missing:
            raise ValueError('Tests not collected by %s:\n%s' % (
                args.runner, '\n'.join('%s:%s' % key for key in sorted(missing))))
        if not expected:
            raise ValueError('No test declarations found')
        print('Collection verified: %d declarations, %d cases (%s)' %
              (len(expected), count, args.runner))
    except (ImportError, OSError, SyntaxError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
