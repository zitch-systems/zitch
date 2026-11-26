"""Sign-up end to end against the bank's documented contract.

The form is driven from the BVN page to the PIN with nothing between it and the
bank replaced: only the HTTP layer is faked, with the request and response shapes
of the bank's published Account Creation (BVN) specification.
"""
import re
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import User
from wallet.models import Wallet

from . import flows
from .flows import sign_onboarding_token
from .test_flow_e2e import FlowContractMixin
from .test_release2 import DETAILS, GOOD_PW, NEW_MSISDN, _exchange, _signup_ob
from .test_release4 import PHONE, SIGNUP_ON

BVN = "22345678901"
TRACKING = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
NUBAN = "0123456789"
BASE = "https://bank.example"
WEMA = {"BASE_URL": BASE, "CHANNEL_ID": "chan-1", "SECURITY_INFO": "sec", "SIMULATION": False,
        "KEYS": {"wallet": "k", "wallet_bvn": "k", "wallet_nin": "k", "face_account": "k",
                 "upgrade": "k", "acct_mgt": "k"}}


def _json(body, status=200):
    m = MagicMock()
    m.json.return_value = body
    m.status_code = status
    m.headers = {}
    m.content = b"{}"
    return m


class Bank:
    """The partner bank, answering as its specification documents."""

    def __init__(self):
        self.calls = []
        self.otp_request = _json({"message": "OTP sent", "status": True, "statusCode": "OK",
                                  "data": {"trackingId": TRACKING}})
        self.opened = False

    def post(self, url, json=None, **kwargs):
        path = url.replace(BASE, "")
        self.calls.append(("POST", path, json, kwargs.get("headers") or {}))
        if path == "/account-creation/api/CustomerAccount/PostPartnershipAccountCreationWithBvn":
            return self.otp_request
        if path == "/account-creation/api/CustomerAccount/ValidateBVNandEnqueueAccountCreation":
            self.opened = json == {"phoneNumber": PHONE, "otp": "123456", "trackingId": TRACKING}
            if not self.opened:
                return _json({"message": "Invalid OTP", "status": False, "statusCode": "BadRequest"}, 400)
            # The specification gives no example message. One that says the
            # account is "processing" is refused by validate_wallet_otp on purpose.
            return _json({"message": "OTP validated successfully", "status": True,
                          "statusCode": "OK", "data": {"trackingId": TRACKING}})
        if path.endswith("/api/CustomerAccount/PartnerDebitRestrictionManagement"):
            return _json({"message": "Done", "status": True, "statusCode": "OK"})
        raise AssertionError(f"undocumented bank call: POST {path}")

    def get(self, url, params=None, **kwargs):
        path = url.replace(BASE, "")
        self.calls.append(("GET", path, params, kwargs.get("headers") or {}))
        if path.endswith("/api/CustomerAccount/GetPartnershipAccountDetails") and self.opened:
            return _json({"message": "Successful", "status": True, "statusCode": "OK",
                          "data": {"accountNumber": NUBAN, "firstName": DETAILS["first_name"],
                                   "lastName": DETAILS["last_name"], "phoneNumber": PHONE}})
        if path.endswith("/api/CustomerAccount/GetPartnershipAccountDetails"):
            return _json({"message": "Account details not found", "status": False,
                          "statusCode": "NotFound", "data": {}}, 404)
        if path.endswith("/api/partnership/partner-account-kyc-status"):
            return _json({"message": "Successful", "status": True, "data": {"tier": "Tier1"}})
        raise AssertionError(f"undocumented bank call: GET {path}")


@override_settings(WHATSAPP_FLOW={**SIGNUP_ON, "DESIGN_V5": True}, WEMA=WEMA)
class SignupAgainstTheBankContract(FlowContractMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.bank = Bank()
        self.sent = []
        for target, value in (
                ("wallet.views._wema_funding_enabled", {"return_value": True}),
                ("utility.providers.partnership_new_business_allowed", {"return_value": True}),
                ("utility.wema.requests.post", {"side_effect": self.bank.post}),
                ("utility.wema.requests.get", {"side_effect": self.bank.get}),
                ("whatsapp.router.reply", {"side_effect": lambda m, text, *a, **k: self.sent.append(text)}),
        ):
            p = patch(target, **value)
            p.start()
            self.addCleanup(p.stop)
        self.ob = _signup_ob()
        self.token = sign_onboarding_token(self.ob)
        with patch("whatsapp.router.sms_live", return_value=True), \
             patch("whatsapp.router.send_sms", return_value={"success": True}) as sms:
            _exchange(self.token, {**DETAILS, "phone": PHONE})
        code = sms.call_args[0][1].split("Zitch: ")[1][:6]
        page = _exchange(self.token, {"phone_code": code, "password": GOOD_PW,
                                      "confirm_password": GOOD_PW})
        self.assertEqual(page["screen"], flows.SIGNUP_BVN)

    def submit(self, **data):
        return _exchange(self.token, data)

    def test_bvn_then_sms_code_then_address_then_pin_opens_the_account(self):
        page = self.submit(bvn=BVN)
        self.assertEqual(page["screen"], flows.SIGNUP_BVN, page)
        self.assertTrue(page["data"]["code_sent"], (page, self.sent))
        method, path, body, headers = self.bank.calls[-1]
        self.assertEqual(body, {"phoneNumber": PHONE, "email": DETAILS["email"].lower(), "bvn": BVN})
        self.assertEqual((headers["x-api-key"], headers["Ocp-Apim-Subscription-Key"]), ("chan-1", "k"))

        page = self.submit(bvn=BVN, code="123456")
        self.assertEqual(page["screen"], flows.SIGNUP_ADDRESS, (page, self.sent))
        user = User.objects.get(phone=PHONE)
        self.assertEqual(Wallet.objects.get(user=user).account_number, NUBAN)
        self.assertTrue(user.bvn_verified)
        self.assertIn("BVN verified", page["data"]["summary"])

        page = self.submit(house_number="12", street="Allen Avenue", state="Lagos", lga="Ikeja")
        self.assertEqual(page["screen"], flows.SIGNUP_PIN, page)
        page = self.submit(pin="482915", confirm_pin="482915")
        self.assertEqual(page["screen"], flows.RESULT_SCREEN, page)
        self.assertIn("Successful", page["data"]["status"])
        user.refresh_from_db()
        self.assertTrue(user.transaction_pin)
        self.assertTrue(any(NUBAN in text for text in self.sent), self.sent)
        # The BVN itself never reaches the chat.
        self.assertFalse(any(BVN in text for text in self.sent))

    def test_the_bank_failing_as_it_did_on_21_november_leaves_the_form_usable(self):
        self.bank.otp_request = _json({"message": "Internal Server Error", "status": False,
                                       "code": "Error", "statusCode": "InternalServerError"}, 400)
        page = self.submit(bvn=BVN)
        self.assertEqual(page["screen"], flows.SIGNUP_BVN)
        self.assertIn("couldn't be reached", page["data"]["error"])
        self.assertFalse(any("Internal Server Error" in text for text in self.sent), self.sent)
        self.assertTrue(any(re.search(r"tap \*Next\*", text) for text in self.sent), self.sent)
        # The bank recovers: the same page, the same BVN, and it goes through.
        self.bank.otp_request = Bank().otp_request
        page = self.submit(bvn=BVN)
        self.assertTrue(page["data"]["code_sent"], (page, self.sent))
        page = self.submit(bvn=BVN, code="123456")
        self.assertEqual(page["screen"], flows.SIGNUP_ADDRESS, (page, self.sent))
