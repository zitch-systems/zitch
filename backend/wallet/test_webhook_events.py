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


@override_settings(WEMA={"CALLBACK_TOKEN": "tok-live", "CALLBACK_TOKEN_PREV": "",
                         "CALLBACK_ENFORCE_IPS": False, "CALLBACK_IPS": [],
                         "AUTH_MAX_AGE": 900, "AUTH_REQUIRE_SECURITY_INFO": False,
                         "SIMULATION": True, "BASE_URL": "https://apiplayground.alat.ng",
                         "KEYS": {}, "SECURITY_INFO": "", "SOURCE_ACCOUNT": ""})
class WemaCallbackForensicsTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, kind, body, token="tok-live"):
        return self.client.post(_url(kind, token), data=json.dumps(body),
                                content_type="application/json")

    def test_accepted_callback_is_recorded_with_its_reference(self):
        # `source` carries the decorator's internal kind ("txn", "auth", "notify"),
        # matching the existing wema_cb_* log lines so the two correlate by eye.
        self._post("transaction", {"transactionReference": "REF-100", "amount": 500})
        row = WebhookEvent.objects.get()
        self.assertEqual(row.source, "wema.txn")
        self.assertEqual(row.outcome, WebhookEvent.ACCEPTED)
        self.assertTrue(row.verified)
        self.assertEqual(row.reference, "REF-100")
        self.assertEqual(row.payload["amount"], 500)

    def test_a_bad_token_is_recorded_and_keeps_no_body(self):
        # The whole reason the log exists: a refused call reaches no handler, so this
        # row is the only evidence it happened at all.
        res = self._post("account", {"data": {"nuban": "0123456789"}}, token="wrong")
        self.assertEqual(res.status_code, 403)
        row = WebhookEvent.objects.get()
        self.assertEqual(row.outcome, WebhookEvent.REJECTED_TOKEN)
        self.assertFalse(row.verified)
        self.assertEqual(row.http_status, 403)
        # Refusing happens before parsing precisely so nothing attacker-chosen is
        # stored; persisting the body would undo that.
        self.assertEqual(row.payload, {})

    def test_a_wrong_method_is_recorded(self):
        res = self.client.get(_url("transaction"))
        self.assertEqual(res.status_code, 405)
        self.assertEqual(WebhookEvent.objects.get().outcome, WebhookEvent.REJECTED_METHOD)

    def test_unparseable_body_is_recorded_as_bad_body(self):
        res = self.client.post(_url("transaction"), data=b"{not json",
                               content_type="application/json")
        row = WebhookEvent.objects.get()
        self.assertEqual(row.outcome, WebhookEvent.BAD_BODY)
        self.assertTrue(row.verified)   # transport auth DID pass; only the body failed
        self.assertEqual(res.status_code, 200)

    def test_a_raising_handler_still_leaves_evidence(self):
        # Recorded in `finally`, so the case where you most need to know a call arrived
        # is the case that still gets a row.
        with mock.patch("wallet.wema_callbacks._authorize_payout",
                        side_effect=RuntimeError("boom")):
            self._post("authorize", {"transactionReference": "REF-ERR"})
        row = WebhookEvent.objects.get()
        self.assertEqual(row.reference, "REF-ERR")
        # The handler catches its own exception and answers 200, and records the reason.
        self.assertEqual(row.action, "denied:error")

    def test_authorisation_decision_is_recorded(self):
        self._post("authorize", {"transactionReference": "REF-UNKNOWN"})
        row = WebhookEvent.objects.get()
        self.assertEqual(row.action, "denied:unknown_reference")

    def test_secrets_are_redacted_from_the_stored_envelope(self):
        self._post("authorize", {"transactionReference": "REF-1",
                                 "securityInfo": "super-secret-value"})
        row = WebhookEvent.objects.get()
        self.assertEqual(row.payload["securityInfo"], "[redacted]")
        # The KEY survives so "the bank sent none" stays distinguishable from
        # "we redacted it".
        self.assertIn("securityInfo", row.payload)

    def test_identity_numbers_are_redacted_at_any_depth(self):
        self._post("account", {"data": {"nuban": "0123456789", "bvn": "22233344455",
                                        "extra": {"NIN": "1234", "transaction_pin": "9999"}}})
        payload = WebhookEvent.objects.get().payload
        self.assertEqual(payload["data"]["bvn"], "[redacted]")
        self.assertEqual(payload["data"]["extra"]["NIN"], "[redacted]")
        # snake_case and camelCase spellings of the same field both match.
        self.assertEqual(payload["data"]["extra"]["transaction_pin"], "[redacted]")
        self.assertEqual(payload["data"]["nuban"], "0123456789")   # not a secret

    def test_a_broken_forensic_log_does_not_break_the_callback(self):
        # A logging bug must never make the bank retry, or deny a payout.
        with mock.patch("whatsapp.models.WebhookEvent.objects.create",
                        side_effect=RuntimeError("table gone")):
            res = self._post("transaction", {"transactionReference": "REF-2"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(WebhookEvent.objects.count(), 0)


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
