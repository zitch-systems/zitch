"""Meta's signed webhook bodies are read even when the strict JSON reader refuses
them, and one that cannot be read at all is acknowledged, not refused.

Production logged Meta's signed events answered 400 "Invalid payload" again and
again: a refused event is retried by Meta for days with the same bytes, and the
endpoint is backed off, delaying live customer messages.
"""
import hashlib
import hmac
import json
from unittest import mock

from django.test import TestCase, override_settings

from whatsapp.views import _parse_meta_body

LIVE = {"MODE": "live", "VERIFY_TOKEN": "v", "TOKEN": "t", "APP_SECRET": "shh",
        "BASE_URL": "x", "PHONE_NUMBER_ID": "phone-1", "BUSINESS_NUMBER": "2348000000000"}


def _event(text):
    return {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "phone-1"},
        "messages": [{"id": "wamid.body-1", "from": "2348011111111",
                      "type": "text", "text": {"body": text}}]}}]}]}


class ParseTests(TestCase):
    def test_a_raw_control_character_in_a_message_is_read(self):
        body = json.dumps(_event("PLACEHOLDER")).replace("PLACEHOLDER", "send 500\x08").encode()
        with self.assertRaises(ValueError):
            json.loads(body)                       # the strict reader refuses it
        event, problem = _parse_meta_body(body)
        self.assertEqual(problem, "")
        self.assertEqual(event["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"],
                         "send 500\x08")

    def test_a_byte_order_mark_is_read(self):
        event, problem = _parse_meta_body(b"\xef\xbb\xbf" + json.dumps(_event("hi")).encode())
        self.assertEqual((problem, bool(event)), ("", True))

    def test_a_byte_that_is_not_utf8_is_replaced_not_refused(self):
        body = json.dumps(_event("PLACEHOLDER")).encode().replace(b"PLACEHOLDER", b"caf\xe9")
        event, problem = _parse_meta_body(body)
        self.assertEqual(problem, "")

    def test_an_unreadable_body_says_where_without_quoting_it(self):
        event, problem = _parse_meta_body(b'{"entry": [1, 2,, 3]}')
        self.assertIsNone(event)
        self.assertIn("at character", problem)
        self.assertNotIn("entry", problem)

    def test_a_list_is_not_an_event(self):
        self.assertEqual(_parse_meta_body(b"[]")[1], "body is a JSON list, not an object")


@override_settings(WHATSAPP=LIVE)
class WebhookTests(TestCase):
    def post(self, body):
        signature = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
        return self.client.post("/webhooks/whatsapp", data=body, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=f"sha256={signature}")

    def test_a_message_with_a_control_character_reaches_the_router(self):
        body = json.dumps(_event("PLACEHOLDER")).replace("PLACEHOLDER", "balance\x1b").encode()
        with mock.patch("whatsapp.views._process") as process:
            response = self.post(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(process.call_args.args[0]["id"], "wamid.body-1")

    def test_a_signed_body_that_cannot_be_read_is_acknowledged_and_reported(self):
        with mock.patch("utility.alerts.alert") as alert:
            response = self.post(b'{"entry": [1, 2,, 3]}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(alert.call_args.kwargs["level"], "error")
        self.assertIn("at character", alert.call_args.kwargs["problem"])

    def test_an_unsigned_body_is_still_refused(self):
        response = self.client.post("/webhooks/whatsapp", data=b"not json",
                                    content_type="application/json",
                                    HTTP_X_HUB_SIGNATURE_256="sha256=deadbeef")
        self.assertEqual(response.status_code, 401)
