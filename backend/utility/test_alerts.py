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
