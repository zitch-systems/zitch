"""Termii SMS rail.

The OTP is the only thing standing between a new user and their account, and the
signup flow deliberately SWALLOWS send failures (anti-enumeration). So a malformed
request here is invisible in production: the user simply never receives a code and
nothing anywhere says why. These tests pin the wire format for that reason.
"""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from utility.providers import _ng_msisdn, send_sms, sms_live, sms_probe

TERMII = {"BASE_URL": "https://v3.api.termii.com", "API_KEY": "tk_live",
          "SENDER_ID": "Zitch", "CHANNEL": "dnd"}
NO_KEY = {**TERMII, "API_KEY": ""}


def _resp(payload, ok=True, status=200):
    r = Mock(status_code=status, ok=ok, content=b"x")
    r.json.return_value = payload
    return r


@override_settings(TERMII=TERMII)
class TermiiWireFormatTests(SimpleTestCase):
    """Termii's request shape, pinned field by field — get any of these wrong and the
    call still looks like it worked."""

    @patch("utility.providers.requests.post")
    def test_api_key_travels_in_the_body_not_a_header(self, mock_post):
        # Termii reads api_key from the JSON body. A bearer header is the reflex for
        # most APIs and here it authenticates as nobody.
        mock_post.return_value = _resp({"message_id": "m-1", "message": "Successfully Sent"})
        send_sms("08031234567", "hello")
        body = mock_post.call_args[1]["json"]
        headers = mock_post.call_args[1]["headers"]
        self.assertEqual(body["api_key"], "tk_live")
        self.assertNotIn("Authorization", headers)

    @patch("utility.providers.requests.post")
    def test_recipient_is_a_bare_string_not_a_list(self, mock_post):
        # `to` is one number as a string; the list-of-recipients shape is rejected.
        mock_post.return_value = _resp({"message_id": "m-1"})
        send_sms("08031234567", "hello")
        self.assertEqual(mock_post.call_args[1]["json"]["to"], "2348031234567")

    @patch("utility.providers.requests.post")
    def test_field_names_and_endpoint(self, mock_post):
        mock_post.return_value = _resp({"message_id": "m-1"})
        send_sms("08031234567", "hello there")
        url = mock_post.call_args[0][0]
        body = mock_post.call_args[1]["json"]
        self.assertEqual(url, "https://v3.api.termii.com/api/sms/send")
        self.assertEqual(body["sms"], "hello there")     # not "message"
        self.assertEqual(body["from"], "Zitch")          # not "sender_name"
        self.assertEqual(body["type"], "plain")

    @patch("utility.providers.requests.post")
    def test_otp_goes_over_the_dnd_route(self, mock_post):
        # Most Nigerian lines are DND-registered and Termii documents "generic" as
        # promotional only — an OTP sent there is accepted and never delivered.
        mock_post.return_value = _resp({"message_id": "m-1"})
        send_sms("08031234567", "code")
        self.assertEqual(mock_post.call_args[1]["json"]["channel"], "dnd")

    @patch("utility.providers.requests.post")
    def test_success_requires_a_message_id(self, mock_post):
        # A 200 with no message_id is Termii rejecting the send (bad sender ID, empty
        # wallet). Treating that as success is how an outage becomes invisible.
        mock_post.return_value = _resp({"message": "Insufficient balance"})
        self.assertFalse(send_sms("08031234567", "x")["success"])
        mock_post.return_value = _resp({"message_id": "m-9", "message": "Successfully Sent"})
        res = send_sms("08031234567", "x")
        self.assertTrue(res["success"])
        self.assertEqual(res["message_id"], "m-9")

    @patch("utility.providers.requests.post", side_effect=ValueError("not json"))
    def test_a_non_json_body_does_not_raise(self, _p):
        self.assertFalse(send_sms("08031234567", "x")["success"])


@override_settings(TERMII=NO_KEY)
class SmsMockModeTests(SimpleTestCase):
    """An unkeyed rail must short-circuit before the network, not attempt a send that
    can only fail. The key is pinned empty here rather than left to the ambient test
    environment, so this keeps testing the branch if a TERMII key ever appears in CI."""

    @patch("utility.providers.requests.post")
    def test_no_key_is_mock_and_calls_nothing(self, mock_post):
        self.assertFalse(sms_live())
        self.assertTrue(send_sms("08031234567", "x")["mock"])
        mock_post.assert_not_called()


@override_settings(TERMII=TERMII)
class SmsProbeTests(SimpleTestCase):
    def test_probe_names_the_rail_and_never_leaks_the_key(self):
        out = sms_probe()
        self.assertEqual(out["config"]["provider"], "termii")
        self.assertTrue(out["config"]["api_key_set"])
        self.assertNotIn("tk_live", str(out))

    @override_settings(TERMII=NO_KEY)
    def test_an_unkeyed_rail_says_so_instead_of_sending(self):
        out = sms_probe("08031234567")
        self.assertFalse(out["config"]["api_key_set"])
        self.assertIn("hint", out)
        self.assertNotIn("send", out)

    def test_a_key_alone_proves_nothing_so_the_probe_asks_for_a_number(self):
        # Configuration is not delivery: without a handset to send to there is
        # nothing to report, and the probe must ask rather than imply success.
        out = sms_probe("")
        self.assertIn("phone", out["hint"].lower())
        self.assertNotIn("send", out)

    @patch("utility.providers.requests.post")
    def test_probe_says_accepted_is_not_delivered(self, mock_post):
        # The distinction that matters: an unapproved sender ID fails AFTER acceptance.
        mock_post.return_value = _resp({"message_id": "m-1"})
        out = sms_probe("08031234567")
        self.assertTrue(out["send"]["ok"])
        # Echo the number actually dialled: a typo'd or mis-normalised MSISDN looks
        # identical to a sender-ID problem from the operator's side otherwise.
        self.assertEqual(out["send"]["to_normalised"], "2348031234567")
        self.assertIn("not that it was delivered", out["note"])

    @patch("utility.providers.requests.post")
    def test_a_rejected_send_explains_the_usual_cause(self, mock_post):
        mock_post.return_value = _resp({"message": "no"})
        out = sms_probe("08031234567")
        self.assertFalse(out["send"]["ok"])
        self.assertIn("whitelisted", out["send"]["hint"])


class MsisdnNormalisationTests(SimpleTestCase):
    def test_every_spelling_reaches_the_same_international_number(self):
        for value in ("08031234567", "8031234567", "2348031234567", "+234 803 123 4567"):
            self.assertEqual(_ng_msisdn(value), "2348031234567")
