"""Tests for the operational alert helper.

Contract: it must ALWAYS log, forward to Sentry only when Sentry is active, and
NEVER raise — even if Sentry itself errors mid-forward. sentry-sdk is installed
under test but never init()'d (settings skips init when TESTING), so the default
client is inactive — the real-world dev/test path.
"""
from unittest import mock

from django.test import TestCase

from utility.alerts import alert


class AlertHelperTests(TestCase):
    def test_noop_without_active_sentry(self):
        # Client inactive under tests => must log without raising and never capture.
        with mock.patch("sentry_sdk.capture_message") as cap:
            alert("something broke", level="error", foo="bar")  # must not raise
        cap.assert_not_called()

    def test_forwards_message_when_active(self):
        client = mock.Mock()
        client.is_active.return_value = True
        with mock.patch("sentry_sdk.get_client", return_value=client), \
             mock.patch("sentry_sdk.capture_message") as cap:
            alert("drift detected", level="error", wallets=3)
        cap.assert_called_once()
        args, kwargs = cap.call_args
        self.assertEqual(args[0], "drift detected")
        self.assertEqual(kwargs.get("level"), "error")

    def test_forwards_exception_when_active(self):
        client = mock.Mock()
        client.is_active.return_value = True
        with mock.patch("sentry_sdk.get_client", return_value=client), \
             mock.patch("sentry_sdk.capture_exception") as cap:
            try:
                raise ValueError("boom")
            except ValueError:
                alert("run crashed", level="fatal", exc=True)
        cap.assert_called_once()

    def test_never_raises_when_sentry_errors(self):
        client = mock.Mock()
        client.is_active.return_value = True
        # new_scope blows up mid-forward — the helper must swallow it, not propagate.
        with mock.patch("sentry_sdk.get_client", return_value=client), \
             mock.patch("sentry_sdk.new_scope", side_effect=RuntimeError("sentry down")):
            alert("x")  # must not raise

    def test_unknown_level_falls_back_to_error(self):
        client = mock.Mock()
        client.is_active.return_value = True
        with mock.patch("sentry_sdk.get_client", return_value=client), \
             mock.patch("sentry_sdk.capture_message") as cap:
            alert("weird", level="bogus")
        self.assertEqual(cap.call_args.kwargs.get("level"), "error")


from django.core.cache import cache
from django.test import override_settings

from utility import alerts as alerts_module

_RESEND = {"BASE_URL": "https://resend.invalid", "API_KEY": "re_test_key",
           "FROM_EMAIL": "Zitch <no-reply@example.test>"}


@override_settings(OPS_ALERT_EMAILS=["ops@example.test"], RESEND=_RESEND,
                   OPS_ALERT_REPEAT_SECONDS=3600)
class OperatorEmailTests(TestCase):
    """Error alerts reach a person by email, once per repeat window, and never
    get in the way of the code that raised them."""

    def setUp(self):
        cache.clear()
        alerts_module._sent.clear()
        alerts_module._seen_locally.clear()
        alerts_module._dropped["capped"] = 0
        patcher = mock.patch("utility.providers.send_email",
                             return_value={"success": True, "raw": {"id": "x"}})
        self.send = patcher.start()
        self.addCleanup(patcher.stop)
        # Retry backoff and Resend spacing, without the wait.
        real_sleep = alerts_module.time.sleep
        sleeper = mock.patch("utility.alerts.time.sleep", side_effect=lambda _s: real_sleep(0.001))
        sleeper.start()
        self.addCleanup(sleeper.stop)

    def sent(self):
        alerts_module._drain(timeout=5)
        return self.send.call_args_list

    def test_an_error_alert_emails_the_operator_with_its_context(self):
        alert("settlement_report: SHORTFALL", level="error", shortfall="2324.38")
        calls = self.sent()
        self.assertEqual(len(calls), 1)
        to, subject, body = calls[0].args[:3]
        self.assertEqual(to, "ops@example.test")
        self.assertIn("SHORTFALL", subject)
        self.assertIn("shortfall: 2324.38", body)

    def test_the_same_alert_is_emailed_once_per_window(self):
        for _ in range(3):
            alert("reconcile_balances: bank NUBAN balance exceeds ledger", level="error")
        self.assertEqual(len(self.sent()), 1)

    def test_counts_in_the_message_do_not_make_it_a_new_alert(self):
        alert("reconcile_wema: all 3 wallet history fetches failed", level="error")
        alert("reconcile_wema: all 4 wallet history fetches failed", level="error")
        self.assertEqual(len(self.sent()), 1)

    def test_warnings_are_not_emailed(self):
        alert("Wema recent credit check could not confirm account history", level="warning")
        self.assertEqual(self.sent(), [])

    def test_fatal_is_emailed(self):
        alert("run crashed", level="fatal")
        self.assertEqual(len(self.sent()), 1)

    def test_a_failing_email_provider_never_reaches_the_caller(self):
        self.send.side_effect = RuntimeError("resend down")
        alert("integrity_check: wallet balance diverges from ledger", level="error")
        self.assertEqual(len(self.sent()), alerts_module._SEND_ATTEMPTS)   # retried quietly

    def test_a_send_that_failed_does_not_silence_the_next_occurrence(self):
        self.send.return_value = {"success": False, "http_status": 429, "retryable": True}
        alert("reconcile_wema: all 40 wallet history fetches failed", level="error")
        self.sent()
        self.send.reset_mock()
        self.send.return_value = {"success": True, "raw": {"id": "y"}}
        alert("reconcile_wema: all 41 wallet history fetches failed", level="error")
        self.assertEqual(len(self.sent()), 1)

    def test_a_refusal_that_cannot_succeed_is_not_retried(self):
        self.send.return_value = {"success": False, "http_status": 403}
        alert("settlement_report: SHORTFALL", level="error")
        self.assertEqual(len(self.sent()), 1)

    def test_the_same_wording_about_another_customer_is_a_new_email(self):
        alert("verified customer has no account number", level="error", user_id=101)
        alert("verified customer has no account number", level="error", user_id=202)
        alert("verified customer has no account number", level="error", user_id=101)
        calls = self.sent()
        self.assertEqual(len(calls), 2)
        self.assertIn("user_id: 202", calls[1].args[2])

    def test_a_changed_set_of_stuck_payouts_is_a_new_email(self):
        alert("reconcile_wema: 1 bank payout(s) still PENDING", level="error", references=["P1"])
        alert("reconcile_wema: 3 bank payout(s) still PENDING", level="error",
              references=["P1", "P2", "P3"])
        self.assertEqual(len(self.sent()), 2)

    def test_an_alert_the_hourly_cap_refused_is_emailed_later(self):
        for n in range(alerts_module._EMAILS_PER_HOUR):
            alert(f"distinct failure {'x' * n}", level="error")
        alert("the one that hit the cap", level="error")
        self.assertEqual(len(self.sent()), alerts_module._EMAILS_PER_HOUR)
        alerts_module._sent.clear()          # the hour has passed
        alert("the one that hit the cap", level="error")
        calls = self.sent()
        self.assertEqual(calls[-1].args[1], "[Zitch error] the one that hit the cap")
        self.assertIn("1 other alert(s) in the last hour were not emailed", calls[-1].args[2])

    def test_the_caller_never_waits_on_the_cache(self):
        import threading

        stalled = threading.Event()
        slow = mock.Mock()
        slow.add.side_effect = lambda *a, **k: stalled.wait(2) or True
        with mock.patch("django.core.cache.cache", slow):
            started = alerts_module.time.monotonic()
            alert("integrity_check: wallet balance diverges from ledger", level="error")
            self.assertLess(alerts_module.time.monotonic() - started, 0.5)   # Redis stalled
            stalled.set()
            self.sent()
        slow.add.assert_called()             # on the sender thread, not the caller's

    def test_a_cache_outage_still_emails_once_per_process(self):
        # The module-level object, so the sender thread sees the outage too
        # (Django's cache proxy is per thread).
        down = mock.Mock(**{name: mock.Mock(side_effect=RuntimeError("redis down"))
                            for name in ("add", "set", "delete", "get")})
        with mock.patch("django.core.cache.cache", down):
            alert("AML cases past their 30-day NFIU filing deadline", level="error")
            alert("AML cases past their 30-day NFIU filing deadline", level="error")
            self.assertEqual(len(self.sent()), 1)
        down.add.assert_called()

    def test_a_flood_of_different_errors_is_capped(self):
        for n in range(alerts_module._EMAILS_PER_HOUR + 5):
            alert(f"distinct failure {'x' * n}", level="error")
        self.assertEqual(len(self.sent()), alerts_module._EMAILS_PER_HOUR)

    @override_settings(OPS_ALERT_EMAILS=[])
    def test_no_recipient_means_no_email(self):
        alert("settlement_report: SHORTFALL", level="error")
        self.assertEqual(self.sent(), [])
        self.assertFalse(alerts_module.operator_email_configured())

    @override_settings(RESEND={**_RESEND, "API_KEY": ""})
    def test_no_email_key_means_no_channel_without_sentry(self):
        self.assertFalse(alerts_module.operator_channel_configured())

    def test_a_recipient_and_a_key_are_a_channel(self):
        self.assertTrue(alerts_module.operator_channel_configured())
