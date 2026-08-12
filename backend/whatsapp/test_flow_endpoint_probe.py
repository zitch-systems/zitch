"""The Flows endpoint's own leg: what Meta asked us, and what we answered.

"Something went wrong. Try again later." is what the customer sees when this
endpoint 500s, fails to decrypt, or is never reached at all — three very
different problems behind one blank panel, none of which left a trace.
"""
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from whatsapp.models import SystemSetting
from whatsapp.views import FLOW_ENDPOINT_KEY


@override_settings(WHATSAPP={"MODE": "sandbox", "TOKEN": "", "PHONE_NUMBER_ID": ""})
class FlowEndpointRecordingTests(TestCase):
    URL = "/webhooks/whatsapp/flow"

    def _post(self):
        return self.client.post(self.URL, data=json.dumps({"encrypted_flow_data": "x"}),
                                content_type="application/json")

    def _record(self):
        raw = SystemSetting.get(FLOW_ENDPOINT_KEY, "")
        parts = (raw.split("|", 3) + ["", "", "", ""])[:4]
        return {"at": parts[0], "action": parts[1], "screen": parts[2], "error": parts[3]}

    def test_a_ping_never_overwrites_the_last_customer_exchange(self):
        """Meta pings every couple of minutes; the record that explains a failed
        Confirm must survive them or forensics have a two-minute shelf life."""
        from whatsapp.views import FLOW_EXCHANGE_KEY

        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "data_exchange", "data": {}}, b"k" * 16, b"i" * 16)), \
             patch("whatsapp.flows.handle_flow_request",
                   return_value={"screen": "SUCCESS", "data": {"message": "x"}}), \
             patch("whatsapp.flows_crypto.encrypt_response", return_value="ok"):
            self._post()
        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "ping"}, b"k" * 16, b"i" * 16)), \
             patch("whatsapp.flows_crypto.encrypt_response", return_value="ok"):
            self._post()
        exchange = SystemSetting.get(FLOW_EXCHANGE_KEY, "")
        self.assertIn("data_exchange", exchange)            # survived the ping
        self.assertIn("ping", SystemSetting.get(FLOW_ENDPOINT_KEY, ""))

    def test_a_healthy_call_records_the_action_and_the_screen_answered(self):
        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "ping"}, b"k" * 16, b"i" * 16)), \
             patch("whatsapp.flows_crypto.encrypt_response", return_value="ok"):
            self.assertEqual(self._post().status_code, 200)
        self.assertEqual(self._record()["action"], "ping")
        self.assertEqual(self._record()["error"], "")

    def test_an_exception_answers_with_a_real_sentence_instead_of_a_500(self):
        """A 500 renders to the customer as a blank panel with Meta's generic
        text. A terminal screen at least tells them what to do next."""
        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "INIT"}, b"k" * 16, b"i" * 16)), \
             patch("whatsapp.flows.handle_flow_request", side_effect=RuntimeError("boom")), \
             patch("whatsapp.flows_crypto.encrypt_response",
                   side_effect=lambda r, k, i: json.dumps(r)) as enc:
            resp = self._post()
        self.assertEqual(resp.status_code, 200)                    # not a 500
        answered = enc.call_args[0][0]                             # what we handed Meta
        self.assertEqual(answered["screen"], "SUCCESS")
        self.assertIn("Reply 8", answered["data"]["message"])
        self.assertIn("RuntimeError", self._record()["error"])

    def test_a_decrypt_failure_is_recorded_rather_than_only_421ing(self):
        from whatsapp.flows_crypto import FlowDecryptError

        with patch("whatsapp.flows_crypto.decrypt_request",
                   side_effect=FlowDecryptError("no key")):
            self.assertEqual(self._post().status_code, 421)
        self.assertIn("decrypt failed", self._record()["error"])

    def test_the_decrypted_payload_never_reaches_the_record(self):
        """It carries the PIN and the identity numbers."""
        with patch("whatsapp.flows_crypto.decrypt_request",
                   return_value=({"action": "data_exchange",
                                  "data": {"pin": "654321", "number": "22222222222"}},
                                 b"k" * 16, b"i" * 16)), \
             patch("whatsapp.flows_crypto.encrypt_response", return_value="ok"):
            self._post()
        raw = SystemSetting.get(FLOW_ENDPOINT_KEY, "")
        self.assertNotIn("654321", raw)
        self.assertNotIn("22222222222", raw)
