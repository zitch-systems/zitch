from unittest.mock import patch

from django.test import SimpleTestCase


class WebStartupQueueRecoveryTests(SimpleTestCase):
    """A deploy must recover rows whose post-webhook daemon was reaped."""

    @patch("whatsapp.jobs.drain_in_background")
    def test_wsgi_startup_kicks_the_bounded_drain(self, drain):
        from zitch_api.wsgi import _recover_whatsapp_queue_on_startup
        from whatsapp.jobs import WEB_DRAIN_BATCH

        # Importing WSGI invokes it once; isolate the explicit assertion from that
        # module-level startup call so this remains deterministic under any test order.
        drain.reset_mock()
        _recover_whatsapp_queue_on_startup()

        drain.assert_called_once_with(batch=WEB_DRAIN_BATCH)

    @patch("whatsapp.jobs.drain_in_background", side_effect=RuntimeError("db unavailable"))
    def test_queue_recovery_cannot_take_down_the_web_process(self, _drain):
        from zitch_api.wsgi import _recover_whatsapp_queue_on_startup

        with self.assertLogs("whatsapp", level="ERROR") as logs:
            _recover_whatsapp_queue_on_startup()

        self.assertIn("wa_startup_drain_failed", " ".join(logs.output))
