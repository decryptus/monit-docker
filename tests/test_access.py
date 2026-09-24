"""Access identity, literal paths, unknown results and per-identity caching."""
import copy
import unittest
from unittest.mock import Mock, patch

from monit_docker.adapters.access import access_identities, access_values, collect_access
from monit_docker.adapters.filesystems import collect_filesystems, directory_groups
from monit_docker.adapters.rules import RuleParser
from monit_docker.core.rules import RuleEvaluator
from monit_docker.domain.errors import MonitoringError, RuleSyntaxError
from monit_docker.domain.models import ContainerSnapshot

IDENTITY = (1000, 1000, (1000, 2000))
OUTPUT = b'monit-access-v1\n1000\n1000\n2000 1000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\ndirectory\n1\n0\n1\n'
GROUPS = {'data': {'paths': ['/data'], 'access': {'uid': 1000, 'gid': 1000, 'groups': [1000, 2000]}}}


class AccessTests(unittest.TestCase):
    def test_explicit_identity_and_groups_are_strict(self):
        self.assertEqual(access_identities(GROUPS), {'data': IDENTITY})
        for identity in ({}, {'uid': '1000', 'gid': 1000, 'groups': [1000]},
                         {'uid': True, 'gid': 1000, 'groups': [1000]},
                         {'uid': 1000, 'gid': 1000, 'groups': []},
                         {'uid': 1000, 'gid': 1000, 'groups': [2000]},
                         {'uid': 1000, 'gid': 1000, 'groups': [1000, 1000]}):
            with self.subTest(identity=identity), self.assertRaises(MonitoringError):
                directory_groups({'data': {'paths': ['/data'], 'access': identity}})

    def test_kernel_results_require_verified_identity_and_unprivileged_capabilities(self):
        self.assertEqual(access_values(OUTPUT, IDENTITY), (1, 0, 1))
        for value in (OUTPUT.replace(b'1000\n1000', b'0\n1000'),
                      OUTPUT.replace(b'2000 1000', b'1000'),
                      OUTPUT.replace(b'0000000000000000', b'0000000000000002', 1),
                      OUTPUT.replace(b'CapEff', b'NoCaps'),
                      OUTPUT.replace(b'directory', b'socket'),
                      OUTPUT[:-2], OUTPUT + b'1\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                access_values(value, IDENTITY)

    def test_probe_uses_literal_positional_path_and_explicit_docker_user(self):
        run = Mock(return_value=OUTPUT)
        path = '/data/a ; $(touch injected)'
        self.assertEqual(collect_access(run, 'api', 'id', path, IDENTITY), (1, 0, 1))
        self.assertEqual(run.call_args.args[-1][-1], path)
        self.assertNotIn(path, run.call_args.args[-1][-2])
        self.assertEqual(run.call_args.kwargs, {'user': '1000:1000'})

    def test_same_path_with_distinct_identities_never_reuses_access_result(self):
        groups = {'data': ('/data',), 'other': ('/data',), 'same': ('/data',)}
        requested = dict.fromkeys(groups, {'fs_writable'})
        identities = {'data': IDENTITY, 'same': IDENTITY, 'other': (1001, 1001, (1001,))}
        with patch('monit_docker.adapters.filesystems.collect_access', side_effect=[(1, 0, 1), (1, 1, 1)]) as probe:
            samples = collect_filesystems(None, 'id', 'web', groups, requested, identities)
        self.assertEqual(probe.call_count, 2)
        self.assertEqual([s.fs_writable for s in samples], [0, 1, 0])
        rule = RuleParser(dir_groups=GROUPS).parse('fs_writable[data] == 0 ? restart')
        self.assertTrue(RuleEvaluator().matches(rule, ContainerSnapshot(filesystems=samples)))

    def test_incomplete_probe_blocks_actions_instead_of_returning_false_success(self):
        with patch('monit_docker.adapters.filesystems.collect_access', side_effect=ValueError('wrong groups')):
            with self.assertRaises(MonitoringError) as caught:
                collect_filesystems(None, 'id', 'web', {'data': ('/data',)}, {'data': {'fs_readable'}}, {'data': IDENTITY})
        self.assertEqual(caught.exception.code, 115)

    def test_cpu_or_disk_checks_do_not_run_access_probes(self):
        with patch('monit_docker.adapters.filesystems.collect_access') as probe:
            with patch('monit_docker.adapters.filesystems._exec_stat', return_value=(1,) * 8):
                samples = collect_filesystems(None, 'id', 'web', {'data': ('/data',)}, {'data': {'disk_percent'}}, {'data': IDENTITY})
            probe.assert_not_called()
            self.assertIsNone(samples[0].fs_readable)

    def test_access_rules_reject_units_ranges_and_missing_identity(self):
        for expression in ('fs_readable[data] > 0 ? restart', 'fs_writable[data] == 2 ? restart',
                           'fs_writable[data] == 1 MB ? restart', 'fs_executable[data] in (0,1) ? restart'):
            with self.subTest(expression=expression), self.assertRaises((RuleSyntaxError, SyntaxError, MonitoringError)):
                RuleParser(dir_groups=GROUPS).parse(expression)
        groups = copy.deepcopy(GROUPS)
        del groups['data']['access']
        with self.assertRaises(MonitoringError):
            RuleParser(dir_groups=groups).parse('fs_writable[data] == 0 ? restart')
