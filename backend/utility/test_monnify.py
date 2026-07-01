"""Tests for the Monnify fund-in provider (utility.monnify) + the credit webhook.

Layers, none needing real Monnify credentials:
- MOCK mode (no keys): reserve/init return success stubs; endpoints work offline.
- SIMULATION: the mock flow is served even in production (MONNIFY_SIMULATION).
- Simulated LIVE: utility.monnify.requests is patched so functions build the real
  request and parse Monnify's {requestSuccessful, responseBody} envelope.
- Webhook: HMAC-SHA512 signature verify + idempotent reserved-account crediting.
Payouts + name enquiry stay on Kora — this module has no disbursement path.
"""
import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import Client, SimpleTestCase, TestCase, override_settings

from utility import monnify, providers
from wallet.models import Wallet
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

MONNIFY_LIVE = {"BASE_URL": "https://sandbox.monnify.com", "API_KEY": "MK_TEST_x",
                "SECRET_KEY": "sk_test_x", "CONTRACT_CODE": "1234567890",
                "REDIRECT_URL": "", "SIMULATION": False}
NOKEY = {**MONNIFY_LIVE, "API_KEY": "", "SECRET_KEY": "", "CONTRACT_CODE": ""}


def _resp(body):
    m = MagicMock()
    m.json.return_value = body
    return m


class MonnifyMockTests(SimpleTestCase):
    def test_mock_mode_active(self):
        self.assertFalse(monnify.monnify_live())

    def test_reserve_and_init_and_verify_mock(self):
        r = monnify.create_virtual_account("ZITCH-WALLET-1", "Ada Eze", "a@b.com", "Ada Eze", bvn="22222222222")
        self.assertTrue(r["success"])
        self.assertTrue(r["account_number"].startswith("99"))
        self.assertTrue(monnify.payment_initialize("a@b.com", 5000, "ZPAY-1")["success"])
        self.assertTrue(monnify.payment_verify("ZPAY-1")["success"])

    def test_webhook_accepts_in_dev_without_keys(self):
        self.assertTrue(monnify.verify_webhook(b"{}", ""))


@override_settings(MONNIFY=MONNIFY_LIVE)
class MonnifyLiveTests(SimpleTestCase):
    def test_monnify_live_true(self):
        self.assertTrue(monnify.monnify_live())

    @patch("utility.monnify.cache")
    @patch("utility.monnify.requests.post")
    def test_reserve_account_live(self, mock_post, mock_cache):
        mock_cache.get.return_value = None
        mock_post.side_effect = [
            _resp({"responseBody": {"accessToken": "tok", "expiresIn": 3000}}),  # auth login
            _resp({"requestSuccessful": True, "responseBody": {
                "accountReference": "ZITCH-WALLET-1", "accountName": "ADA EZE",
                "accounts": [{"bankName": "Wema Bank", "accountNumber": "1234567890", "bankCode": "035"}]}}),
        ]
        r = monnify.create_virtual_account("ZITCH-WALLET-1", "ADA EZE", "a@b.com", "ADA EZE", bvn="22222222222")
        self.assertTrue(r["success"])
        self.assertEqual(r["account_number"], "1234567890")
        self.assertEqual(r["bank_name"], "Wema Bank")
        body = mock_post.call_args_list[1].kwargs["json"]  # the reserved-accounts POST
        self.assertEqual(body["bvn"], "22222222222")
        self.assertEqual(body["contractCode"], "1234567890")
        self.assertTrue(body["getAllAvailableBanks"])

    @patch("utility.monnify.cache")
    @patch("utility.monnify.requests.post")
    def test_init_transaction_live(self, mock_post, mock_cache):
        mock_cache.get.return_value = None
        mock_post.side_effect = [
            _resp({"responseBody": {"accessToken": "tok"}}),
            _resp({"requestSuccessful": True, "responseBody": {
                "checkoutUrl": "https://pay.monnify/x", "paymentReference": "ZPAY-1"}}),
        ]
        r = monnify.payment_initialize("a@b.com", 5000, "ZPAY-1")
        self.assertTrue(r["success"])
        self.assertEqual(r["authorization_url"], "https://pay.monnify/x")
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["amount"], 5000.0)  # naira, float

    @patch("utility.monnify.cache")
    @patch("utility.monnify.requests.get")
    @patch("utility.monnify.requests.post")
    def test_payment_verify_live_paid(self, mock_post, mock_get, mock_cache):
        mock_cache.get.return_value = None
        mock_post.return_value = _resp({"responseBody": {"accessToken": "tok"}})
        mock_get.return_value = _resp({"requestSuccessful": True, "responseBody": {
            "paymentStatus": "PAID", "amountPaid": 5000, "paymentReference": "ZPAY-1"}})
        r = monnify.payment_verify("ZPAY-1")
        self.assertTrue(r["success"])
        self.assertEqual(r["amount_naira"], 5000)

    def test_verify_webhook_signature(self):
        body = b'{"eventType":"SUCCESSFUL_TRANSACTION"}'
        sig = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
        self.assertTrue(monnify.verify_webhook(body, sig))
        self.assertFalse(monnify.verify_webhook(body, "wrong"))
        self.assertFalse(monnify.verify_webhook(body, ""))


@override_settings(DEBUG=False, TESTING=False)
class MonnifySimulationTests(SimpleTestCase):
    """Prod without keys fails closed; MONNIFY_SIMULATION serves the mock flow."""

    def test_prod_without_keys_fails_closed(self):
        with override_settings(MONNIFY={**NOKEY, "SIMULATION": False}):
            self.assertFalse(monnify.monnify_simulation())
            r = monnify.create_virtual_account("ZITCH-WALLET-1", "Ada", "a@b.com", "Ada")
            self.assertFalse(r["success"])
            self.assertIn("not configured", r["message"].lower())
            self.assertFalse(monnify.verify_webhook(b"{}", ""))  # unsigned callback rejected

    def test_simulation_serves_mock_in_prod(self):
        with override_settings(MONNIFY={**NOKEY, "SIMULATION": True}):
            self.assertTrue(monnify.monnify_simulation())
            r = monnify.create_virtual_account("ZITCH-WALLET-1", "Ada", "a@b.com", "Ada")
            self.assertTrue(r["success"])
            self.assertTrue(r["account_number"].startswith("99"))
            self.assertEqual(monnify.monnify_diagnostics()["status"], "simulation")


class PaymentProviderSelectionTests(SimpleTestCase):
    def test_explicit_monnify(self):
        with override_settings(PAYMENT_PROVIDER="monnify"):
            self.assertEqual(providers.payment_provider(), "monnify")

    def test_explicit_kora(self):
        with override_settings(PAYMENT_PROVIDER="kora"):
            self.assertEqual(providers.payment_provider(), "kora")

    def test_auto_defaults_kora_without_monnify(self):
        with override_settings(PAYMENT_PROVIDER="", MONNIFY={**NOKEY, "SIMULATION": False}):
            self.assertEqual(providers.payment_provider(), "kora")

    def test_auto_picks_monnify_when_live(self):
        with override_settings(PAYMENT_PROVIDER="", MONNIFY=MONNIFY_LIVE):
            self.assertEqual(providers.payment_provider(), "monnify")


@override_settings(MONNIFY={**NOKEY, "SIMULATION": True}, PAYMENT_PROVIDER="monnify")
class MonnifyFundInEndToEndTests(TestCase):
    """Provision a Monnify reserved account under simulation, then credit it via the
    inflow webhook — the whole fund-in loop without real Monnify keys."""

    def test_provision_then_webhook_credits_once(self):
        client = Client()
        user, token = make_user("08077700001", "mon@zitch.app")
        r = client.post("/api/wallet/account/create/",
                        data=json.dumps({"access_token": token, "bvn": "22222222222"}),
                        content_type="application/json")
        self.assertEqual(r.status_code, 200)
        acct = r.json()["account_number"]
        self.assertTrue(acct.startswith("99"))
        wallet = get_or_create_wallet(user)
        self.assertEqual(wallet.account_reference, f"ZITCH-WALLET-{user.id}")

        event = {"eventType": "SUCCESSFUL_TRANSACTION", "eventData": {
            "product": {"type": "RESERVED_ACCOUNT", "reference": wallet.account_reference},
            "accountReference": wallet.account_reference,
            "transactionReference": "MNFY-TX-1", "amountPaid": 5000,
            "destinationAccountInformation": {"accountNumber": acct}}}
        wr = client.post("/api/fund/monnify/webhook/", data=json.dumps(event),
                         content_type="application/json")
        self.assertEqual(wr.status_code, 200)
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("5000"))
        # Redelivered webhook must not double-credit.
        client.post("/api/fund/monnify/webhook/", data=json.dumps(event), content_type="application/json")
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("5000"))
