"""The last Flow rejection, parked where an operator can reach it."""
from unittest.mock import patch

from django.test import TestCase, override_settings

from whatsapp.models import SystemSetting
from whatsapp.providers import last_flow_error, send_flow

LIVE = dict(
    WHATSAPP={"MODE": "live", "TOKEN": "t", "PHONE_NUMBER_ID": "1",
              "BASE_URL": "https://graph.facebook.com/v21.0"},
    WHATSAPP_FLOW={"FLOW_ID": "999", "PRIVATE_KEY": "k"},
)


class _Rejection:
    status_code = 400
    ok = False
    content = b"{}"

    def json(self):
        return {"error": {"code": 131009, "message": "Parameter value is not valid",
                          "error_data": {"details": "screen IDENTITY_SCREEN not found"}}}


class _Accepted:
    status_code = 200
    ok = True
    content = b"{}"

    def json(self):
        return {"messages": [{"id": "wamid.X"}]}


@override_settings(**LIVE)
class LastFlowErrorTests(TestCase):

    def _send(self, response):
        with patch("whatsapp.providers.requests.post", return_value=response):
            return send_flow("2348011112222", "tok", "Verify your identity", "body",
                             "IDENTITY_SCREEN", {"summary": "s", "label": "BVN", "error": ""},
                             cta="Enter securely")

    def test_nothing_is_reported_before_any_rejection(self):
        self.assertEqual(last_flow_error(), {})

    def test_a_rejection_records_metas_own_words_and_the_screen(self):
        self.assertFalse(self._send(_Rejection())["success"])
        err = last_flow_error()
        self.assertEqual(err["screen"], "IDENTITY_SCREEN")
        self.assertEqual(err["code"], "131009")
        self.assertIn("not found", err["detail"])       # the actionable half
        self.assertTrue(err["at"])

    def test_an_accepted_send_records_nothing(self):
        self.assertTrue(self._send(_Accepted())["success"])
        self.assertEqual(last_flow_error(), {})

    def test_the_recipient_is_never_stored(self):
        self._send(_Rejection())
        self.assertNotIn("2348011112222", SystemSetting.get("wa_last_flow_error", ""))

    def test_a_diagnostics_failure_never_breaks_the_send_path(self):
        with patch("whatsapp.models.SystemSetting.set", side_effect=RuntimeError("db down")):
            self.assertFalse(self._send(_Rejection())["success"])   # returned, not raised


class SmsRouteIsVisibleTests(TestCase):
    """The Termii route on /healthz. Accepted is not delivered, and the two
    fields that decide delivery were previously unreadable without a token."""

    @override_settings(TERMII={"SENDER_ID": "Zitch", "CHANNEL": "generic",
                               "API_KEY": "k", "BASE_URL": "https://v3.api.termii.com"})
    def test_healthz_reports_the_sender_id_and_route(self):
        body = self.client.get("/healthz").json()["integrations"]
        self.assertEqual(body["sms_sender_id"], "Zitch")
        self.assertEqual(body["sms_channel"], "generic")   # the promotional route

    @override_settings(TERMII={"SENDER_ID": "Zitch", "CHANNEL": "dnd",
                               "API_KEY": "k", "BASE_URL": "https://v3.api.termii.com"})
    def test_no_secret_reaches_the_page(self):
        raw = self.client.get("/healthz").content.decode()
        self.assertNotIn("k", raw.split('"sms_sender_id"')[0][-30:])
        self.assertNotIn("API_KEY", raw)
