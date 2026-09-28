"""Application behavior must be available with command interfaces blocked."""
import ast
import unittest
import tempfile
import monit_docker
import os
from pathlib import Path
import subprocess
import sys
import textwrap

PACKAGE_ROOT = Path(monit_docker.__file__).resolve().parent
ROOT = PACKAGE_ROOT.parent
INTERFACES = frozenset(('cli.py', '__main__.py'))


def check_package(root):
    paths = list(root.rglob('*.py'))
    assert paths and (root / 'application.py').is_file(), 'Empty architecture scan'
    for path in paths:
        if path.name in INTERFACES:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or '']
                names += [(node.module + '.' if node.module else 'monit_docker.') + alias.name
                          for alias in node.names]
            assert not any(name == 'monit_docker.cli' or name.startswith('monit_docker.cli.')
                           for name in names), (str(path), node.lineno)


class ApplicationArchitectureTests(unittest.TestCase):
    def test_package_services_do_not_import_cli_even_inside_callbacks(self):
        check_package(PACKAGE_ROOT)

    def test_guard_rejects_empty_scans_and_lazy_cli_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(AssertionError, 'Empty architecture scan'):
                check_package(root)
            (root / 'application.py').write_text('def callback():\n    from monit_docker import cli\n')
            with self.assertRaises(AssertionError):
                check_package(root)

    def test_scenario_composition_cycles_and_state_operations_without_cli(self):
        program = textwrap.dedent('''
            import importlib.abc
            import sys
            class BlockInterfaces(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname in ('monit_docker.cli', 'monit_docker.__main__', 'curses'):
                        raise AssertionError('Interface imported: ' + fullname)
            sys.meta_path.insert(0, BlockInterfaces())

            from dataclasses import replace
            from pathlib import Path
            from tempfile import TemporaryDirectory
            from unittest.mock import Mock, patch
            from monit_docker.adapters.scenarios import validate_scenario
            from monit_docker.adapters.validation import check_configuration
            from monit_docker.adapters.state import LocalState
            from monit_docker.application import MonitoringApplication
            from monit_docker.application_state import StateOperations
            from monit_docker.composition import build_application
            from monit_docker.core.policy import restart_key
            from monit_docker.domain.errors import ActionRejected, MonitoringError
            from monit_docker.job import JobOptions
            from monit_docker.observation import Observation
            from monit_docker.domain.rules import CycleResult
            observed = Observation(lambda: CycleResult((), ()))
            observed.refresh()
            assert observed.snapshot()['collection_error'] is None

            config = {'scenarios': {'web': {'mode': 'stats', 'select': {'name': ['web-*']}}}}
            job = validate_scenario(config, 'web')
            assert isinstance(job, JobOptions) and job.name == ('web-*',)
            with patch('monit_docker.composition.client_factory', return_value=Mock()):
                composed = build_application(job, config=config, use_rules=False)
            assert type(composed) is MonitoringApplication
            assert composed.cycle.__self__ is composed
            assert composed.manual_action.__self__ is composed
            with TemporaryDirectory() as tmp:
                path = str(Path(tmp) / 'state.json')
                identifier = 'a' * 64
                audit = Mock()
                operations = StateOperations(LocalState, audit, clock=lambda: 100)
                result = operations.maintenance(path, identifier, 60, 'operator')
                assert result['maintenance_until'] == 160
                engine = Mock()
                options = JobOptions(subcommand='serve', state_file=path, allow_maintenance=True)
                app = MonitoringApplication(engine, (), options, audit, LocalState, clock=lambda: 200)
                observer = Mock()
                app.cycle(observer)
                assert engine.run_once.call_args.kwargs['on_action'] is observer
                with LocalState(path) as state:
                    assert identifier not in state.maintenance
                    state.reserve_restart(restart_key(identifier), 1)
                assert operations.reset_restarts(path, identifier, 'operator')['status'] == 'rearmed'
                try:
                    operations.reset_restarts(path, identifier, 'operator')
                    raise AssertionError('Missing restart budget accepted')
                except MonitoringError as error:
                    assert error.code == 110
                def manual(container_id, command, claim, **callbacks):
                    assert container_id == identifier and command == 'restart'
                    if not claim(container_id):
                        raise ActionRejected('cooldown')
                engine.run_manual_action.side_effect = manual
                app.manual_action(identifier, 'restart')
                try:
                    app.manual_action(identifier, 'restart')
                    raise AssertionError('Manual cooldown bypassed')
                except ActionRejected:
                    pass
                before = Path(path).read_bytes()
                dry = MonitoringApplication(engine, (), replace(options, dry_run=True), audit,
                                            LocalState, clock=lambda: 300)
                dry.cycle(observer)
                assert Path(path).read_bytes() == before
                for invalid in (identifier + '\\n', 'é' * 64, 'a' * 63):
                    try:
                        operations.maintenance(path, invalid, 10, 'operator')
                        raise AssertionError('Invalid identifier accepted')
                    except MonitoringError:
                        pass
            assert 'monit_docker.cli' not in sys.modules
        ''')
        result = subprocess.run([sys.executable, '-c', program], cwd=str(ROOT),
                                env=dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', '')),
                                capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
