import asyncio
import io
import unittest
from unittest.mock import Mock, patch
from textual.widgets import DataTable, Static
from dwho.tui.textual import StatusLine
from monit_docker import cli
from monit_docker.textual_demo import demo_app
from monit_docker.textual_tui import run


class TextualTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)

    async def test_real_adapter_search_journal_and_literal_details(self):
        app = demo_app()
        async with app.run_test(size=(156, 46)) as pilot:
            self.assertEqual(app.query_one(DataTable).row_count, 3)
            await pilot.press('/'); await pilot.press('p', 'o', 's', 't'); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 1)
            await pilot.click('#dw-nav-1'); await pilot.pause()
            self.assertEqual(app.query_one(DataTable).row_count, 2)
            self.assertIn('synthetic', str(app.query_one('.dw-detail-text', Static).render()))
            app.observation.data['journal_error'] = 'invalid_cursor'
            app.refresh_snapshot()
            self.assertEqual(app.query_one('#dw-notice', StatusLine).state, 'failure')

    async def test_stale_collection_does_not_display_healthy_rows(self):
        app = demo_app()
        async with app.run_test() as pilot:
            app.observation.data.update(stale=True, containers=[], age=100)
            app.refresh_snapshot()
            self.assertEqual(app.query_one(DataTable).row_count, 0)
            self.assertEqual(app.query_one('#dw-notice', StatusLine).state, 'warning')

    def test_cli_opt_in_and_no_client_creation_without_terminal(self):
        self.assertEqual(cli.argv_parse_check(['tui']).ui, 'curses')
        self.assertEqual(cli.argv_parse_check(['tui', '--ui', 'textual']).ui, 'textual')
        factory = Mock()
        with patch('sys.stdin.isatty', return_value=False), patch('sys.stderr', io.StringIO()):
            self.assertEqual(run(factory), 2)
        factory.assert_not_called()

    def test_launch_always_closes_observation(self):
        observation = Mock()
        with patch('dwho.cli.require_terminal'), patch.dict('os.environ', TERM='xterm'), patch('monit_docker.textual_tui.MonitorApp') as app:
            self.assertEqual(run(lambda: observation), 0)
            observation.start.assert_called_once()
            app.return_value.run.assert_called_once()
            observation.close.assert_called_once()

    async def test_refresh_does_not_access_observation_after_app_shutdown(self):
        app = demo_app()
        async with app.run_test() as pilot:
            await pilot.pause()
        with patch.object(app.observation, 'snapshot') as snapshot:
            app.refresh_snapshot()
            snapshot.assert_not_called()
