"""Prembly ownership codes use identity contacts with mandatory real SMS."""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import IdentityProof, User
from accounts.views import _confirm_identity_ownership_challenge, _start_identity_ownership_challenge

PHONE = "2348031234567"
EMAIL = "holder@record.example"
RAW = "12345678901"
RECORD = {"success": True, "provider": "prembly", "phone": PHONE, "email": EMAIL,
          "first_name": "Ada", "last_name": "Eze"}


@override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                   DEBUG=True, TESTING=True)
class PremblyOtpChannelsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="dual-otp", phone="08010000001",
            email="signup@example.com", first_name="Ada", last_name="Eze", email_verified=True)
        self.key = f"kyc_identity:bvn:{self.user.pk}"

    def start(self, *, record=None, sms=None, email=None, sms_live=True, email_live=True):
        with patch("accounts.views.sms_live", return_value=sms_live), \
                patch("accounts.views.email_live", return_value=email_live), \
                patch("accounts.views._otp_code", return_value="724915"), \
                patch("accounts.views.send_sms", return_value={"success": True} if sms is None else sms) as send_sms, \
                patch("accounts.views.send_email", return_value={"success": True} if email is None else email) as send_email:
            response = _start_identity_ownership_challenge(self.user, "bvn", RAW,
                                                           RECORD if record is None else record)
        return response, send_sms, send_email

    def test_same_code_goes_only_to_record_phone_and_record_email(self):
        response, sms, email = self.start()
        self.assertEqual(response.status_code, 200)
        sms.assert_called_once()
        email.assert_called_once()
        self.assertEqual(sms.call_args.args[0], PHONE)
        self.assertEqual(email.call_args.args[0], EMAIL)
        self.assertEqual(sms.call_args.args[1], email.call_args.args[2])
        self.assertIn("724915", sms.call_args.args[1])
        self.assertEqual(email.call_args.kwargs["timeout"], 5)
        body = json.loads(response.content)
        self.assertEqual(body["delivery_channels"], ["sms", "email"])
        self.assertEqual(body["delivery_status"], {"sms": "accepted", "email": "accepted"})
        self.assertFalse(body["delivery_partial"])
        self.assertIn("h***@record.example", body["delivery"])
        self.assertIn("•••••4567", body["delivery"])
        for secret in (RAW, PHONE, EMAIL, "724915", self.user.email):
            self.assertNotIn(secret, response.content.decode())
            self.assertNotIn(secret, str(cache.get(self.key)))
        self.assertFalse(IdentityProof.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.address_verified)
        recovered, error = _confirm_identity_ownership_challenge(self.user, "bvn", "724915")
        self.assertEqual(recovered, RAW)
        self.assertIsNone(error)
        self.assertIsNone(cache.get(self.key))

    def test_missing_or_invalid_record_email_never_uses_verified_signup_email(self):
        for value in (None, "", "broken", [EMAIL], {"email": EMAIL}, "a@@example.com",
                      "Ada <holder@record.example>", "a@example.com\nBcc:other@example.com"):
            with self.subTest(value=value):
                response, sms, email = self.start(record={**RECORD, "email": value})
                self.assertEqual(response.status_code, 200)
                sms.assert_called_once()
                email.assert_not_called()
                body = json.loads(response.content)
                self.assertEqual(body["delivery_channels"], ["sms"])
                self.assertEqual(body["delivery_status"]["email"], "not_available")
                self.assertFalse(body["delivery_partial"])

    def test_email_failure_or_mock_leaves_usable_sms_challenge_and_partial_notice(self):
        for result in ({"success": False}, {"success": True, "mock": True},
                       {"success": "true"}, {"success": 1}):
            with self.subTest(result=result):
                response, sms, email = self.start(email=result)
                self.assertEqual(response.status_code, 200)
                sms.assert_called_once()
                email.assert_called_once()
                body = json.loads(response.content)
                self.assertEqual(body["delivery_status"]["email"], "failed")
                self.assertEqual(body["delivery_channels"], ["sms"])
                self.assertTrue(body["delivery_partial"])
                self.assertIn("registered phone", body["delivery_notice"])
                self.assertNotIn("registered email", body["delivery"])
                self.assertEqual(_confirm_identity_ownership_challenge(self.user, "bvn", "724915"), (RAW, None))

    def test_email_unconfigured_is_reported_without_mock_send(self):
        response, _, email = self.start(email_live=False)
        email.assert_not_called()
        body = json.loads(response.content)
        self.assertEqual(body["delivery_status"]["email"], "unavailable")
        self.assertTrue(body["delivery_partial"])
        self.assertIsNotNone(cache.get(self.key))

    def test_sms_failure_truthy_or_mock_never_sends_email_or_creates_challenge(self):
        for result in ({"success": False}, {"success": True, "mock": True},
                       {"success": "true"}, {"success": 1}):
            with self.subTest(result=result):
                response, sms, email = self.start(sms=result)
                self.assertEqual(response.status_code, 503)
                sms.assert_called_once()
                email.assert_not_called()
                self.assertIsNone(cache.get(self.key))

    def test_unconfigured_sms_remains_closed_even_in_debug(self):
        response, sms, email = self.start(sms_live=False)
        self.assertEqual(response.status_code, 503)
        sms.assert_not_called()
        email.assert_not_called()
        self.assertIsNone(cache.get(self.key))

    def test_invalid_missing_phone_never_falls_back_to_signup_phone_or_email(self):
        for value in (None, "", PHONE + "999", {"phone": PHONE}, "not-a-phone", "14155551234"):
            with self.subTest(value=value):
                response, sms, email = self.start(record={**RECORD, "phone": value})
                self.assertEqual(response.status_code, 503)
                sms.assert_not_called()
                email.assert_not_called()
                self.assertIsNone(cache.get(self.key))

    def test_mock_or_unsuccessful_identity_cannot_issue_real_ownership_challenge(self):
        for record in ({**RECORD, "mock": True}, {**RECORD, "success": False}):
            with self.subTest(record=record):
                response, sms, email = self.start(record=record)
                self.assertEqual(response.status_code, 503)
                sms.assert_not_called()
                email.assert_not_called()
                self.assertIsNone(cache.get(self.key))

    def test_optional_email_exception_preserves_accepted_sms(self):
        with patch("accounts.views.sms_live", return_value=True), \
                patch("accounts.views.email_live", return_value=True), \
                patch("accounts.views.send_sms", return_value={"success": True}), \
                patch("accounts.views.send_email", side_effect=ValueError("unreadable response")):
            response = _start_identity_ownership_challenge(self.user, "bvn", RAW, RECORD)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)["delivery_status"]["email"], "failed")
        self.assertIsNotNone(cache.get(self.key))
