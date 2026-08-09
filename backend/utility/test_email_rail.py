"""The email rail, checked the way it actually fails.

`RESEND_API_KEY` being set is what every existing check looked at, and it stayed
green throughout an incident where Resend refused every single send because the
sender domain was not verified on the account the key belonged to. Callers drop
the result by design (anti-enumeration), so the refusal reached nobody: the
customer was told a code was on its way and waited on mail that was never
accepted. These tests pin the two halves of the fix — the refusal is logged, and
the rail can be probed for the condition rather than for the key.
"""
from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from utility.providers import email_probe, send_email, sender_domain

_CFG = {"BASE_URL": "https://api.resend.test", "API_KEY": "re_x",
        "FROM_EMAIL": "Zitch <no-reply@send.zitch.ng>"}


class _Resp:
    def __init__(self, status=200, payload=None, content=b"x"):
        self.status_code, self._payload, self.content = status, payload, content

    @property
    def ok(self):
        return 200 <= self.status_code < 300

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


@override_settings(RESEND=_CFG)
class SenderDomainTests(SimpleTestCase):
    def test_it_parses_the_display_name_form(self):
        self.assertEqual(sender_domain(), "send.zitch.ng")

    @override_settings(RESEND=dict(_CFG, FROM_EMAIL="x"))
    def test_a_value_with_no_address_is_not_a_domain(self):
        """rpartition returns the whole string as the tail when '@' is absent, so
        a misconfigured value would otherwise be probed as if it were a domain."""
        self.assertEqual(sender_domain(), "")


@override_settings(RESEND=_CFG)
class SendEmailLoggingTests(SimpleTestCase):
    def test_a_refusal_is_logged_with_the_provider_reason(self):
        refusal = {"statusCode": 403, "message": "The send.zitch.ng domain is not verified"}
        with patch("utility.providers.requests.post",
                   return_value=_Resp(403, refusal)) as post:
            with self.assertLogs("zitch", level="WARNING") as logs:
                out = send_email("k@zitch.test", "s", "m")
        post.assert_called_once()
        self.assertFalse(out["success"])
        line = "\n".join(logs.output)
        self.assertIn("email_rejected", line)
        self.assertIn("domain is not verified", line)
        # The fault is the sender's; the recipient has no business in the log.
        self.assertNotIn("k@zitch.test", line)

    def test_a_successful_send_logs_nothing(self):
        with patch("utility.providers.requests.post",
                   return_value=_Resp(200, {"id": "e_1"})):
            with patch("utility.providers.log") as log:
                out = send_email("k@zitch.test", "s", "m")
        self.assertTrue(out["success"])
        log.warning.assert_not_called()


@override_settings(RESEND=_CFG)
class EmailProbeTests(SimpleTestCase):
    def _probe(self, resp):
        with patch("utility.providers.requests.get", return_value=resp):
            return email_probe()

    def test_a_verified_sender_domain_is_ok(self):
        out = self._probe(_Resp(200, {"data": [
            {"name": "send.zitch.ng", "status": "verified"}]}))
        self.assertTrue(out["ok"])
        self.assertNotIn("hint", out)

    def test_an_unverified_sender_domain_is_named_along_with_what_is_verified(self):
        """The exact shape of the incident: a valid key on an account that has a
        verified domain — just not the one being sent from."""
        out = self._probe(_Resp(200, {"data": [
            {"name": "gymflow.ng", "status": "verified"},
            {"name": "send.zitch.ng", "status": "pending"}]}))
        self.assertFalse(out["ok"])
        self.assertIn("send.zitch.ng", out["hint"])
        self.assertIn("gymflow.ng", out["hint"])

    def test_an_account_with_no_verified_domain_says_so(self):
        out = self._probe(_Resp(200, {"data": []}))
        self.assertFalse(out["ok"])
        self.assertIn("no verified domain", out["hint"])

    def test_a_rejected_key_is_reported_as_a_key_problem(self):
        """Distinct from an unverified domain: 'set' and 'valid' are different
        claims, and the operator needs to know which one failed."""
        out = self._probe(_Resp(401, {"message": "API key is invalid"}))
        self.assertFalse(out["ok"])
        self.assertIn("rejected the key", out["hint"])

    def test_an_unreachable_provider_does_not_raise(self):
        with patch("utility.providers.requests.get",
                   side_effect=requests.RequestException("boom")):
            out = email_probe()
        self.assertFalse(out["ok"])
        self.assertIn("unreachable", out["hint"])

    @override_settings(RESEND=dict(_CFG, API_KEY=""))
    def test_an_unkeyed_rail_makes_no_network_call(self):
        with patch("utility.providers.requests.get") as get:
            out = email_probe()
        get.assert_not_called()
        self.assertIn("RESEND_API_KEY unset", out["hint"])

    def test_the_probe_never_returns_the_key(self):
        out = self._probe(_Resp(200, {"data": []}))
        self.assertNotIn("re_x", str(out))
        self.assertTrue(out["config"]["api_key_set"])
