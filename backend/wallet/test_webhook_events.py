"""The inbound-callback forensic log.

What it must record that nothing else does: the calls we REFUSED (they never reach a
handler), and the calls that ERRORED (a handler that raises leaves no success trace).
What it must never do: break a callback, or persist a secret.
"""
import json
from decimal import Decimal
from unittest import mock

from django.test import Client, TestCase, override_settings

from whatsapp.models import WebhookEvent


def _url(kind, token="tok-live"):
    return f"/webhooks/wema/{kind}/{token}"




class WebhookEventModelTests(TestCase):
    def test_rows_are_immutable(self):
        row = WebhookEvent.objects.create(source="wema.account", reference="R1")
        row.action = "tampered"
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            row.delete()
        self.assertEqual(WebhookEvent.objects.get(pk=row.pk).action, "")

    def test_redact_preserves_structure(self):
        out = WebhookEvent.redact({"a": [{"pin": "1", "keep": "2"}], "cvv": "3"})
        self.assertEqual(out, {"a": [{"pin": "[redacted]", "keep": "2"}],
                               "cvv": "[redacted]"})

    def test_redact_tolerates_non_dict_leaves(self):
        self.assertEqual(WebhookEvent.redact([1, "x", None]), [1, "x", None])


class WhatsAppWebhookForensicsTests(TestCase):
    """WhatsApp is deliberately recorded as a DESCRIPTOR, not an envelope: a chat body
    can contain the customer's message text — including a transaction PIN typed in
    reply — and a forensic table is the wrong place for that."""

    def _post(self, body, signed=True):
        with mock.patch("whatsapp.views.verify_signature", return_value=signed):
            return self.client.post("/webhooks/whatsapp", data=json.dumps(body),
                                    content_type="application/json")

    def test_message_text_is_never_stored(self):
        self._post({"entry": [{"changes": [{"value": {"messages": [
            {"id": "wamid.1", "type": "text", "from": "2348011112222",
             "text": {"body": "1234"}}]}}]}]})
        row = WebhookEvent.objects.get()
        self.assertEqual(row.payload["messages"], [{"id": "wamid.1", "type": "text"}])
        self.assertNotIn("1234", json.dumps(row.payload))
        self.assertNotIn("2348011112222", json.dumps(row.payload))
        self.assertEqual(row.action, "messages:1 statuses:0")

    def test_a_bad_signature_is_recorded(self):
        res = self._post({"entry": []}, signed=False)
        self.assertEqual(res.status_code, 401)
        row = WebhookEvent.objects.get()
        self.assertEqual(row.outcome, WebhookEvent.REJECTED_SIGNATURE)
        self.assertFalse(row.verified)
        self.assertEqual(row.payload, {})
