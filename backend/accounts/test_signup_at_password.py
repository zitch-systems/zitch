"""App sign-up: the account is created when the password is set (17 Nov 2026).

The app checks the SMS code first (``check_only``), shows "Create a password",
then sends the same code with the password. Only that second call creates the
account, so a sign-up abandoned at the password page leaves nothing behind.
"""
import json
from unittest.mock import patch

from django.test import Client, TestCase

from .models import OTP, User

PHONE = "08055501234"
CODE = "246813"
STRONG = "Zitch!2026pw"


class SignupAtPasswordTests(TestCase):
    def setUp(self):
        self.client = Client()
        with patch("accounts.views._otp_code", return_value=CODE):
            self.post({"phone": PHONE, "email": ""}, path="/api/phone_verification/")

    def post(self, payload, path="/api/verify_otp/"):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_check_only_confirms_the_code_and_creates_nothing(self):
        res, body = self.post({"phone": PHONE, "otp": CODE, "check_only": True})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["verified"])
        self.assertNotIn("access_token", body)
        self.assertFalse(User.objects.filter(phone=PHONE).exists())
        self.assertFalse(OTP.objects.get(phone=PHONE).used)   # still usable with the password

    def test_check_only_refuses_a_wrong_code(self):
        res, body = self.post({"phone": PHONE, "otp": "000000", "check_only": True})
        self.assertNotEqual(res.status_code, 200)
        self.assertFalse(body.get("verified", False))
        self.assertFalse(User.objects.filter(phone=PHONE).exists())

    def test_the_password_call_creates_the_account_with_it(self):
        self.post({"phone": PHONE, "otp": CODE, "check_only": True})
        res, body = self.post({"phone": PHONE, "otp": CODE, "first_name": "Ada",
                               "last_name": "Okafor", "password": STRONG})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        user = User.objects.get(phone=PHONE)
        self.assertTrue(user.check_password(STRONG))
        self.assertTrue(user.phone_verified)
        self.assertEqual(user.tier, 1)                 # no Tier 0
        self.assertEqual(user.get_full_name(), "Ada Okafor")
        self.assertTrue(OTP.objects.get(phone=PHONE).used)

    def test_a_weak_password_is_refused_before_the_code_is_used(self):
        res, body = self.post({"phone": PHONE, "otp": CODE, "password": "abc"})
        self.assertNotEqual(res.status_code, 200)
        self.assertEqual(body["code"], "weak_password")
        self.assertFalse(User.objects.filter(phone=PHONE).exists())
        self.assertFalse(OTP.objects.get(phone=PHONE).used)
        res, _ = self.post({"phone": PHONE, "otp": CODE, "password": STRONG})
        self.assertEqual(res.status_code, 200)

    def test_an_old_app_build_without_either_field_still_signs_up(self):
        res, body = self.post({"phone": PHONE, "otp": CODE})
        self.assertEqual(res.status_code, 200)
        self.assertIn("access_token", body)
        user = User.objects.get(phone=PHONE)
        self.assertFalse(user.has_usable_password())   # set on the next screen, as before
