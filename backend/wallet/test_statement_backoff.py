"""Statement readers leave the bank alone after a failed read.

From 15:05 WAT on 14 November 2026 Wema's gateway answered every call with
something unreadable. Only a 429 used to pause the readers, so the worker kept
asking for the same customer's statement every five seconds for hours, and
had been asking at that rate since the evening before.
"""
import os
from unittest import skipIf
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from wallet import deposit_sync, reconciliation
from wallet.reconciliation import STATEMENT_BACKOFF_KEY, note_statement_read


class BackoffTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def pauses(self, errors):
        """The backoff set after each error, in seconds."""
        seen = []
        with patch.object(reconciliation.cache, "set", wraps=cache.set) as setter:
            for error in errors:
                note_statement_read(error)
                pause = next((c.kwargs["timeout"] for c in reversed(setter.call_args_list)
                              if c.args[0] == STATEMENT_BACKOFF_KEY), None)
                seen.append(pause)
                setter.reset_mock()
        return seen

    def test_an_unreadable_answer_backs_off_not_only_a_429(self):
        note_statement_read("history_unavailable")
        self.assertTrue(cache.get(STATEMENT_BACKOFF_KEY))
        self.assertEqual(deposit_sync.BACKOFF_KEY, STATEMENT_BACKOFF_KEY)

    def test_each_failure_doubles_the_pause_up_to_fifteen_minutes(self):
        self.assertEqual(self.pauses(["history_unavailable"] * 6 + ["history_rate_limited"]),
                         [60, 120, 240, 480, 900, 900, 900])

    def test_a_read_that_succeeds_starts_the_count_again(self):
        self.pauses(["history_unavailable"] * 3)
        note_statement_read("")
        self.assertEqual(self.pauses(["history_unavailable"]), [60])

    def test_errors_about_our_own_data_are_not_the_banks(self):
        for error in ("account_changed", "history_opening_review"):
            with self.subTest(error=error):
                note_statement_read(error)
                self.assertIsNone(cache.get(STATEMENT_BACKOFF_KEY))

    def test_a_cache_outage_does_not_stop_a_read(self):
        with patch.object(reconciliation.cache, "get", side_effect=RuntimeError("down")):
            note_statement_read("history_unavailable")  # does not raise


class PollBacksOffTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_the_worker_stops_asking_while_the_bank_answers_unreadably(self):
        with patch("wallet.reconciliation.wema_provisioned_wallets") as wallets, \
             patch("wallet.reconciliation.reconcile_recent_credits",
                   return_value=(0, "history_unavailable")) as read, \
             patch("utility.alerts.alert"):
            wallets.return_value.select_related.return_value.order_by.return_value \
                .filter.return_value.first.return_value = type("W", (), {"pk": 7})()
            reconciliation.poll_recent_wema_credits(60)
            cache.delete("wema:recent-credit:poll")  # the next interval comes round
            reconciliation.poll_recent_wema_credits(60)
        read.assert_called_once()


class PollSurvivesACacheOutageTests(TestCase):
    """With the shared cache down, every poll used to raise before reading an
    account, and a deposit waited for the ten-minute cron."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        reconciliation._poll_local.update(next_at=0.0, cursor=0)
        self.addCleanup(reconciliation._poll_local.update, next_at=0.0, cursor=0)

    def test_the_poll_keeps_reading_on_its_own_clock(self):
        down = RuntimeError("cache down")
        with patch.object(reconciliation.cache, "get", side_effect=down), \
             patch.object(reconciliation.cache, "add", side_effect=down), \
             patch.object(reconciliation.cache, "set", side_effect=down), \
             patch("wallet.reconciliation.wema_provisioned_wallets") as wallets, \
             patch("wallet.reconciliation.reconcile_recent_credits",
                   return_value=(1, "")) as read, \
             patch("utility.alerts.alert"):
            wallets.return_value.select_related.return_value.order_by.return_value \
                .filter.return_value.first.return_value = type("W", (), {"pk": 7})()
            reconciliation.poll_recent_wema_credits(60)
            reconciliation.poll_recent_wema_credits(60)      # same interval: throttled
        read.assert_called_once()
        self.assertEqual(reconciliation._poll_local["cursor"], 7)


class DefaultIntervalTests(SimpleTestCase):
    @skipIf(os.environ.get("WEMA_RECENT_CREDIT_POLL_SECONDS"), "interval set by the environment")
    def test_the_worker_polls_once_a_minute_by_default(self):
        self.assertEqual(settings.WEMA_RECENT_CREDIT_POLL_SECONDS, 60)
