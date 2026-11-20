"""Explicit preview consent, bounded admission and monitoring independence."""
import copy
import threading
import unittest
from unittest.mock import Mock

from monit_docker.incident import ExplanationError
from monit_docker.incident_session import IncidentSessions, MAX_PREVIEWS
from incident_fixtures import status, analysis, IDENTIFIER, NOW


class IncidentSessionTests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.provider = Mock()
        self.provider.complete.return_value = dict(analysis=analysis(), usage={})
        self.loader = Mock(return_value=self.provider)
        self.service = IncidentSessions(self.loader, clock=lambda: self.now, timer=lambda: self.now)
        self.data = status()

    def preview(self, actor='alice'):
        return self.service.preview(self.data, IDENTIFIER, actor)

    def explain(self, preview, actor='alice'):
        return self.service.explain(self.data, IDENTIFIER, preview['preview_id'], actor)

    def test_preview_never_loads_provider_and_submission_sends_exact_preview(self):
        preview = self.preview()
        self.loader.assert_not_called()
        self.now += 5
        response = self.explain(preview)
        self.assertEqual(response['status'], 'completed')
        self.assertEqual(self.provider.complete.call_args.args[1], preview['context'])
        self.assertEqual(response['context'], preview['context'])
        self.provider.complete.assert_called_once()
        with self.assertRaisesRegex(ExplanationError, 'invalid_preview'):
            self.explain(preview)

    def test_changed_evidence_snapshot_and_freshness_prevent_calls(self):
        for change in ('metric', 'timestamp', 'freshness'):
            with self.subTest(change=change):
                self.data = status()
                preview = self.preview()
                if change == 'metric':
                    self.data['containers'][0]['mem_percent'] = 97
                elif change == 'timestamp':
                    self.data['last_success_at'] += 1
                else:
                    self.data['last_cycle_success'] = False
                with self.assertRaisesRegex(ExplanationError, 'evidence_changed'):
                    self.explain(preview)
        self.loader.assert_not_called()

    def test_preview_is_actor_bound_bounded_expiring_and_not_client_mutable(self):
        preview = self.preview()
        with self.assertRaisesRegex(ExplanationError, 'invalid_preview'):
            self.explain(preview, 'bob')
        original = copy.deepcopy(preview['context'])
        preview['context']['evidence'].clear()
        self.explain(preview)
        self.assertEqual(self.provider.complete.call_args.args[1], original)
        for _ in range(MAX_PREVIEWS):
            self.preview()
        with self.assertRaisesRegex(ExplanationError, 'preview_capacity'):
            self.preview()
        self.now += 61
        self.assertIsNotNone(self.preview()['preview_id'])

    def test_expired_and_malformed_preview_cannot_contact_provider(self):
        preview = self.preview()
        self.now += 60
        with self.assertRaisesRegex(ExplanationError, 'preview_expired'):
            self.explain(preview)
        for key in (None, [], 'a' * 64 + '\n', 'é' * 64):
            with self.assertRaisesRegex(ExplanationError, 'invalid_preview'):
                self.explain({'preview_id': key})
        self.loader.assert_not_called()

    def test_provider_failure_consumes_consent_and_rate_budget(self):
        preview = self.preview()
        self.provider.complete.side_effect = ExplanationError('provider_timeout')
        with self.assertRaisesRegex(ExplanationError, 'provider_timeout'):
            self.explain(preview)
        with self.assertRaisesRegex(ExplanationError, 'invalid_preview'):
            self.explain(preview)
        second = self.preview()
        with self.assertRaisesRegex(ExplanationError, 'ai_rate_limited'):
            self.explain(second)
        self.provider.complete.assert_called_once()
        self.now += 61
        self.provider.complete.side_effect = None
        self.assertEqual(self.explain(self.preview())['status'], 'completed')

    def test_one_inflight_request_does_not_block_preview_or_monitoring(self):
        entered, release = threading.Event(), threading.Event()
        def complete(*args):
            entered.set()
            self.assertTrue(release.wait(3))
            return dict(analysis=analysis(), usage={})
        self.provider.complete.side_effect = complete
        preview = self.preview()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.explain(preview)))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            second = self.preview()
            with self.assertRaisesRegex(ExplanationError, 'ai_busy'):
                self.explain(second)
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]['status'], 'completed')

    def test_journal_change_requires_new_preview_and_only_filtered_context_is_sent(self):
        records = []
        self.service.evidence_options = lambda *args: dict(records=records, history_more=True)
        preview = self.preview()
        records.append(dict(container_id=IDENTIFIER, category='action', action='restart',
                            result='failed', timestamp='2026-11-20T08:00:00Z'))
        with self.assertRaisesRegex(ExplanationError, 'evidence_changed'):
            self.explain(preview)
        self.loader.assert_not_called()
