"""Named scenarios exercise real CLI parsing, policies and offline validation."""

import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import yaml
from docker.errors import APIError

from monit_docker import cli
from monit_docker.adapters.configuration import Configuration
from monit_docker.adapters.scenarios import validate_scenario
from monit_docker.adapters.state import LocalState
from monit_docker.adapters.validation import check_configuration, ConfigurationCheckError
import test_monit_docker as legacy


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.yml'
        self.state = self.root / 'state.json'
        self.audit = self.root / 'audit.jsonl'
        self.scenario = dict(mode='cron', select={'name': 'web-*'},
                             rules=['mem_percent > 70 ? restart'], cooldown=300,
                             **{'state-file': str(self.state)})
        self.document = {'scenarios': {'web-guard': self.scenario}}
        self.client = Mock()
        self.addCleanup(patch.stopall)
        patch.object(cli, 'MONIT_DOCKER_CONFIG', None).start()
        self.connection = patch.object(cli.docker, 'from_env', return_value=self.client).start()
        self.other_connection = patch.object(cli.docker, 'DockerClient', side_effect=AssertionError('unexpected client')).start()
        self.fresh_containers()
        self.write()

    def fresh_containers(self):
        self.web, self.db = legacy.container('web-one'), legacy.container('db-one')
        self.web.id, self.db.id = 'a' * 64, 'b' * 64
        self.client.containers.list.return_value = [self.web, self.db]

    def write(self):
        self.config.write_text(yaml.safe_dump(self.document))

    def invoke(self, *args):
        arguments = ['-c', str(self.config), '--runtimedir', '',
                     '--logfile', str(self.root / 'missing' / 'log'),
                     '--audit-file', str(self.audit)] + list(args)
        out, err = io.StringIO(), io.StringIO()
        with patch.object(cli.sys, 'stdout', out), patch.object(cli.sys, 'stderr', err):
            code = cli.main(cli.argv_parse_check(arguments))
        return code, out.getvalue(), err.getvalue()

    def test_run_targets_only_selected_containers_and_records_automatic_action(self):
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()
        self.db.restart.assert_not_called()
        events = [json.loads(line) for line in self.audit.read_text().splitlines()]
        self.assertTrue(any(event['result'] == 'succeeded' and event['source'] == 'automatic'
                            and event['actor'] == 'cron' for event in events))

    def test_same_state_retains_cooldown_across_invocations(self):
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.fresh_containers()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_not_called()

    def test_dry_run_never_executes_or_consumes_restart_budget(self):
        code, output, _ = self.invoke('run', 'web-guard', '--dry-run')
        self.assertEqual(code, 0)
        self.assertTrue(output)
        self.web.restart.assert_not_called()
        self.assertFalse(self.state.exists())
        self.fresh_containers()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()
        before = self.state.read_bytes()
        self.fresh_containers()
        self.assertEqual(self.invoke('run', 'web-guard', '--dry-run')[0], 0)
        self.assertEqual(self.state.read_bytes(), before)

    def test_configured_simulation_cannot_be_disabled_by_run(self):
        self.scenario['dry-run'] = True
        self.write()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_not_called()

    def test_restart_limit_and_maintenance_are_preserved(self):
        self.scenario.update(cooldown=0, **{'max-restarts': 1})
        self.write()
        with LocalState(str(self.state)) as state:
            state.set_maintenance(self.web.id, 900, time.time())
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_not_called()
        with LocalState(str(self.state)) as state:
            state.set_maintenance(self.web.id, 0, time.time())
        self.fresh_containers()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()
        self.fresh_containers()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_not_called()

    def test_action_failure_keeps_existing_exit_code(self):
        self.web.restart.side_effect = APIError('failure')
        self.assertEqual(self.invoke('run', 'web-guard')[0], 116)

    def test_state_lock_failure_does_not_collect_or_execute(self):
        with LocalState(str(self.state)):
            self.assertEqual(self.invoke('run', 'web-guard')[0], 117)
        self.connection.assert_not_called()
        self.web.restart.assert_not_called()

    def test_container_group_and_status_selection_with_default_mode(self):
        self.scenario.pop('mode')
        self.scenario['select'] = {'group': 'frontend', 'status': 'running'}
        self.document['ctn-groups'] = {'frontend': {'match': ['name:web-*']}}
        self.write()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()
        self.db.restart.assert_not_called()

    def test_scenario_audit_settings_override_global_defaults(self):
        configured = self.root / 'configured.jsonl'
        self.scenario['audit-file'] = str(configured)
        self.write()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.assertTrue(configured.exists())
        self.assertFalse(self.audit.exists())

    def test_stats_mode_has_no_state_or_action(self):
        self.document['scenarios']['metrics'] = dict(mode='stats', select={'name': 'web-*'},
                                                     resources=['mem_usage', 'cpu_percent'])
        self.write()
        code, output, _ = self.invoke('run', 'metrics')
        self.assertEqual(code, 0)
        self.assertIn('web-one', json.loads(output))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.audit.exists())
        self.web.restart.assert_not_called()

    def test_serve_reuses_existing_runner_and_config_snapshot(self):
        self.scenario.update(mode='serve', interval=15, **{'trigger-after': 60, 'max-gap': 45})
        self.write()
        with patch.object(cli.MonitDockerSubCmdServe, '__call__', return_value=0) as runner:
            with patch.object(Configuration, 'load', wraps=Configuration(str(self.config)).load) as load:
                self.assertEqual(self.invoke('run', 'web-guard', '--dry-run')[0], 0)
                self.assertEqual(load.call_count, 1)
            runner.assert_called_once_with()
        self.connection.assert_not_called()

    def test_list_show_and_check_config_are_offline_and_do_not_write(self):
        with patch.object(cli, 'WatchedFileHandler', side_effect=AssertionError('log opened')):
            with patch.object(cli.helpers, 'make_dirs', side_effect=AssertionError('runtime created')):
                for args in (('scenario', 'list'), ('scenario', 'show', 'web-guard'),
                             ('check-config', '--output', 'json')):
                    with self.subTest(args=args):
                        code, output, _ = self.invoke(*args)
                        self.assertEqual(code, 0, output)
                        self.assertTrue(json.loads(output))
        self.connection.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertFalse(Path(str(self.state) + '.lock').exists())
        self.assertFalse(self.audit.exists())

    def test_unknown_scenario_fails_without_docker(self):
        for args in (('run', 'missing'), ('scenario', 'show', 'missing')):
            self.assertEqual(self.invoke(*args)[0], 110)
        self.connection.assert_not_called()

    def test_invalid_fields_modes_rules_and_policies_fail_before_docker(self):
        examples = [dict(select={}), dict(select={'name': []}), dict(all=True),
                    dict(select={'group': 'web', 'name': 'db-*'}), dict(select={'group': 'missing'}),
                    dict(select={'name': '~['}), dict(mode='monit'), dict(mode='unknown'),
                    dict(rules=[]), dict(rules=['restart', '@missing']),
                    dict(rules=['cpu_percent > 1, pid > 1.5 ? restart']),
                    dict(client='missing'), dict(cooldown=-1), dict(cooldown=True),
                    dict(cooldown=float('nan')), {'max-restarts': 0}, {'max-restarts': True},
                    {'state-file': ''}, {'state-file': None}, {'trigger-after': 60},
                    {'dry-run': 'false'}, {'unexpected': 1},
                    dict(mode='stats'), dict(mode='serve', interval=0),
                    dict(mode='serve', interval=30, **{'trigger-after': 60, 'max-gap': 15})]
        for changes in examples:
            with self.subTest(changes=changes):
                self.document['scenarios']['web-guard'] = dict(self.scenario, **changes)
                self.write()
                self.assertEqual(self.invoke('run', 'web-guard')[0], 110)
                with self.assertRaises(ConfigurationCheckError):
                    check_configuration(str(self.config))
        self.connection.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_global_selection_override_is_rejected(self):
        with self.assertRaises(SystemExit) as error:
            self.invoke('--name', 'db-*', 'run', 'web-guard')
        self.assertEqual(error.exception.code, 2)
        self.connection.assert_not_called()

    def test_explicit_all_and_names_starting_with_options_are_literal(self):
        self.scenario.pop('select')
        self.scenario['all'] = True
        self.write()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.db.restart.assert_called_once_with()
        self.fresh_containers()
        self.scenario.pop('all')
        self.scenario['select'] = {'name': '--help'}
        self.write()
        self.assertEqual(self.invoke('run', 'web-guard')[0], 114)
        self.web.restart.assert_not_called()

    def test_imports_templates_condition_and_action_aliases(self):
        entry = copy.deepcopy(self.scenario)
        entry['rules'] = ['@busy ? @recycle']
        entry['cooldown'] = '${vars["delay"]}'
        (self.root / 'scenarios.yml').write_text(yaml.safe_dump({'web-guard': entry}))
        self.document.update(vars={'delay': 42}, scenarios={'@import_scenario': 'scenarios.yml'},
                             conditions={'busy': {'expr': ['mem_percent > 70']}},
                             commands={'recycle': {'exec': ['restart']}})
        self.write()
        self.assertEqual(check_configuration(str(self.config))['scenarios'], 1)
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()

    def test_scenario_local_variables_render_in_main_configuration(self):
        self.scenario['vars'] = {'threshold': 70, 'delay': 42}
        self.scenario['rules'] = ['mem_percent > ${vars["threshold"]} ? restart']
        self.scenario['cooldown'] = '${vars["delay"]}'
        self.write()
        config = Configuration(str(self.config)).load()
        self.assertEqual(validate_scenario(config, 'web-guard').cooldown, 42)
        self.assertEqual(self.invoke('run', 'web-guard')[0], 0)
        self.web.restart.assert_called_once_with()

    def test_directory_groups_with_access_identity_validate_offline(self):
        self.document['dir-groups'] = {'app': {'paths': ['/data'],
                                               'access': {'uid': 1000, 'gid': 1000, 'groups': [1000]}}}
        self.document['scenarios']['access'] = dict(mode='stats', select={'name': 'web-*'},
                                                    resources=['fs_writable[app]', 'fs_executable[app]'])
        self.write()
        self.assertEqual(check_configuration(str(self.config))['scenarios'], 2)
        self.document['scenarios']['access']['resources'] = ['fs_writable[missing]']
        self.write()
        self.assertEqual(self.invoke('run', 'access')[0], 110)
        self.connection.assert_not_called()

    def test_access_resources_without_identity_fail_all_offline_entry_points(self):
        self.document['dir-groups'] = {'app': {'paths': ['/data']}}
        for mode in ('stats', 'serve'):
            for resource in ('fs_readable[app]', 'fs_writable[app]', 'fs_executable[app]'):
                self.document['scenarios']['access'] = dict(mode=mode, all=True, resources=[resource])
                self.write()
                for args in (('check-config', '--output', 'json'), ('scenario', 'list'),
                             ('scenario', 'show', 'access'), ('run', 'access')):
                    with self.subTest(mode=mode, resource=resource, args=args), \
                            patch('monit_docker.adapters.http.run_server') as server:
                        code, out, err = self.invoke(*args)
                        self.assertEqual(code, 110, out + err)
                        self.assertIn('scenarios.access.resources', out + err)
                        self.assertIn('access identity', out + err)
                        server.assert_not_called()
        self.connection.assert_not_called()
        self.assertFalse(self.state.exists())
        self.assertFalse(self.audit.exists())

    def test_non_access_filesystem_resources_need_no_identity(self):
        self.document['dir-groups'] = {'app': {'paths': ['/data']}}
        self.document['scenarios']['disk'] = dict(mode='stats', all=True,
                                                resources=['disk_percent[app]', 'inode_percent[app]', 'fs_mode[app]'])
        self.write()
        self.assertEqual(self.invoke('check-config', '--output', 'json')[0], 0)
        self.assertEqual(self.invoke('scenario', 'show', 'disk')[0], 0)
        self.connection.assert_not_called()

    def test_inline_configuration_and_existing_default_path_work(self):
        self.config.unlink()
        with patch.object(cli, 'MONIT_DOCKER_CONFIG', yaml.safe_dump(self.document)):
            self.assertEqual(self.invoke('scenario', 'list')[0], 0)
        self.write()
        with patch.object(cli, 'MONIT_DOCKER_CONFFILE', str(self.config)):
            options = cli.argv_parse_check(['scenario', 'show', 'web-guard'])
        with patch.object(cli.sys, 'stdout', io.StringIO()):
            self.assertEqual(cli.main(options), 0)

    def test_invalid_options_do_not_print_secret_values(self):
        secret = 'private-value-not-for-diagnostics'
        self.scenario.update(mode='serve', bind=secret)
        self.write()
        code, out, err = self.invoke('run', 'web-guard')
        self.assertEqual(code, 110)
        self.assertNotIn(secret, out + err)

    def test_stat_dry_run_and_malformed_scenario_names_are_rejected(self):
        self.document['scenarios']['metrics'] = dict(mode='stats', all=True)
        self.write()
        self.assertEqual(self.invoke('run', 'metrics', '--dry-run')[0], 110)
        for name in ('../web-guard', 'Web', 'a' * 65):
            with self.subTest(name=name):
                self.document['scenarios'] = {name: self.scenario}
                self.write()
                self.assertEqual(self.invoke('scenario', 'list')[0], 110)
        self.connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
