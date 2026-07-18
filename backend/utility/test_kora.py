"""Tests for the Korapay client (utility.kora): mock-mode fallbacks, the live
request/response mapping for funding / virtual accounts / payouts / balances /
Nigeria KYC, and the webhook HMAC-SHA256 signature check.

Live calls are exercised by patching utility.kora.requests.{post,get} so no network
is touched; KORA_LIVE supplies a secret key so the non-mock path runs.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from utility import kora

KORA_LIVE = {"BASE_URL": "https://api.korapay.com", "SECRET_KEY": "sk_test_x",
             "PUBLIC_KEY": "pk_test_x", "REDIRECT_URL": "", "VA_BANK_CODE": "035",
             "FUND_WEBHOOK_URL": "", "PAYOUT_WEBHOOK_URL": "", "SIMULATION": False}


def _resp(payload: dict, ok: bool = True):
    """A fake requests.Response returning `payload` from .json()."""
    r = MagicMock()
    r.ok = ok
    r.json.return_value = payload
    r.content = json.dumps(payload).encode()
    return r


class KoraModeTests(SimpleTestCase):
    def test_not_live_without_key(self):
        self.assertFalse(kora.kora_live())

    @override_settings(KORA=KORA_LIVE)
    def test_live_with_key(self):
        self.assertTrue(kora.kora_live())

    def test_mock_payment_initialize_returns_sentinel_url(self):
        out = kora.payment_initialize("a@b.com", 1000, "ZPAY1", name="Ada")
        self.assertTrue(out["success"])
        self.assertTrue(out["mock"])
        self.assertIn("mock://kora/checkout/ZPAY1", out["authorization_url"])

    def test_mock_create_virtual_account_deterministic(self):
        a = kora.create_virtual_account("ZITCH-WALLET-1", "Ada Eze", "a@b.com", "Ada Eze", bvn="22212345678")
        b = kora.create_virtual_account("ZITCH-WALLET-1", "Ada Eze", "a@b.com", "Ada Eze", bvn="22212345678")
        self.assertTrue(a["success"] and a["mock"])
        self.assertEqual(a["account_number"], b["account_number"])  # deterministic per reference
        self.assertEqual(a["accounts"][0]["account_number"], a["account_number"])


@override_settings(KORA=KORA_LIVE)
class KoraFundingTests(SimpleTestCase):
    def test_payment_initialize_maps_checkout_url(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "message": "ok",
                                       "data": {"checkout_url": "https://checkout.korapay.com/abc",
                                                "reference": "ZPAY1"}})) as m:
            out = kora.payment_initialize("a@b.com", 1500, "ZPAY1", name="Ada")
        self.assertTrue(out["success"])
        self.assertEqual(out["authorization_url"], "https://checkout.korapay.com/abc")
        body = m.call_args.kwargs["json"]
        self.assertEqual(body["currency"], "NGN")
        self.assertEqual(body["reference"], "ZPAY1")

    def test_payment_verify_success(self):
        with patch("utility.kora.requests.get",
                   return_value=_resp({"status": True, "data": {"status": "success", "amount": 1500,
                                                                "reference": "ZPAY1"}})):
            out = kora.payment_verify("ZPAY1")
        self.assertTrue(out["success"])
        self.assertEqual(out["amount_naira"], 1500)

    def test_payment_verify_processing_is_not_paid(self):
        with patch("utility.kora.requests.get",
                   return_value=_resp({"status": True, "data": {"status": "processing"}})):
            out = kora.payment_verify("ZPAY1")
        self.assertFalse(out["success"])

    def test_create_virtual_account_maps_fields_and_sends_bvn(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {
                       "account_number": "1234567890", "bank_name": "Wema Bank",
                       "account_name": "ADA EZE", "account_reference": "ZITCH-WALLET-5"}})) as m:
            out = kora.create_virtual_account("ZITCH-WALLET-5", "ADA EZE", "a@b.com", "ADA EZE",
                                              bvn="22212345678")
        self.assertTrue(out["success"])
        self.assertEqual(out["account_number"], "1234567890")
        self.assertEqual(out["bank_name"], "Wema Bank")
        self.assertEqual(out["accounts"][0]["account_number"], "1234567890")
        body = m.call_args.kwargs["json"]
        self.assertTrue(body["permanent"])
        self.assertEqual(body["bank_code"], "035")
        self.assertEqual(body["kyc"]["bvn"], "22212345678")


@override_settings(KORA=KORA_LIVE)
class KoraPayoutTests(SimpleTestCase):
    def test_resolve_account_returns_name(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"account_name": "ADA EZE"}})) as m:
            out = kora.resolve_account("0123456789", "058")
        self.assertTrue(out["success"])
        self.assertEqual(out["name"], "ADA EZE")
        body = m.call_args.kwargs["json"]
        self.assertEqual(body["bank"], "058")
        self.assertEqual(body["account"], "0123456789")

    def test_disburse_processing_holds(self):
        # Kora returns processing on accept — success=True, status=processing => held.
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"status": "processing",
                                                                "reference": "ZTRF1"}})):
            out = kora.disburse(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "processing")

    def test_disburse_success_is_delivered(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"status": "success", "reference": "ZTRF1"}})):
            out = kora.disburse(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "success")

    def test_disburse_rejected_fails(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": False, "message": "Insufficient balance"}, ok=False)):
            out = kora.disburse(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        self.assertFalse(out["success"])
        self.assertNotIn("pending", out)  # a definitive rejection, refundable

    def test_disburse_network_error_is_pending_not_failure(self):
        import requests as rq
        with patch("utility.kora.requests.post", side_effect=rq.RequestException("timeout")):
            out = kora.disburse(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])  # ambiguous — hold the debit

    def test_verify_payout_maps_status(self):
        with patch("utility.kora.requests.get",
                   return_value=_resp({"status": True, "data": {"status": "success"}})):
            self.assertTrue(kora.verify_payout("ZTRF1")["success"])
        with patch("utility.kora.requests.get",
                   return_value=_resp({"status": True, "data": {"status": "processing"}})):
            self.assertTrue(kora.verify_payout("ZTRF1")["pending"])
        with patch("utility.kora.requests.get",
                   return_value=_resp({"status": True, "data": {"status": "failed"}})):
            out = kora.verify_payout("ZTRF1")
            self.assertFalse(out["success"])
            self.assertEqual(out["status"], "failed")


@override_settings(KORA=KORA_LIVE)
class KoraKycTests(SimpleTestCase):
    def test_verify_bvn_success_with_matching_name(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"first_name": "ADA", "last_name": "EZE"}})):
            out = kora.verify_bvn("22222222222", name="Ada Eze")
        self.assertTrue(out["success"])

    def test_verify_bvn_rejects_clear_name_mismatch(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"first_name": "JOHN", "last_name": "DOE"}})):
            out = kora.verify_bvn("22222222222", name="Ada Eze")
        self.assertFalse(out["success"])

    def test_verify_bvn_no_name_supplied_accepts(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"first_name": "JOHN", "last_name": "DOE"}})):
            out = kora.verify_bvn("22222222222")
        self.assertTrue(out["success"])

    def test_verify_bvn_rejects_bad_length(self):
        self.assertFalse(kora.verify_bvn("123")["success"])

    def test_verify_nin_success(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"first_name": "ADA"}})):
            self.assertTrue(kora.verify_nin("12345678901")["success"])

    def test_verify_vnin_success(self):
        with patch("utility.kora.requests.post",
                   return_value=_resp({"status": True, "data": {"first_name": "ADA"}})):
            self.assertTrue(kora.verify_vnin("AB123456789CDEFG")["success"])


class KoraKycFailClosedTests(SimpleTestCase):
    """Identity never mock-passes in production, even with simulation on."""

    @override_settings(DEBUG=False, TESTING=False)
    def test_bvn_fails_closed_in_prod_without_keys(self):
        out = kora.verify_bvn("22222222222", name="Ada Eze")
        self.assertFalse(out["success"])

    def test_bvn_mocks_in_tests_without_keys(self):
        out = kora.verify_bvn("22222222222", name="Ada Eze")
        self.assertTrue(out["success"])
        self.assertTrue(out["mock"])


@override_settings(KORA=KORA_LIVE)
class KoraWebhookTests(SimpleTestCase):
    def _sign(self, data: dict) -> str:
        body = json.dumps(data, separators=(",", ":")).encode()
        return hmac.new(b"sk_test_x", body, hashlib.sha256).hexdigest()

    def test_valid_signature_accepts(self):
        data = {"reference": "ZPAY1", "amount": 1500, "status": "success"}
        raw = json.dumps({"event": "charge.success", "data": data}).encode()
        self.assertTrue(kora.verify_webhook(raw, self._sign(data)))

    def test_bad_signature_rejects(self):
        raw = json.dumps({"event": "charge.success", "data": {"reference": "ZPAY1"}}).encode()
        self.assertFalse(kora.verify_webhook(raw, "deadbeef"))

    def test_missing_signature_rejects_when_live(self):
        raw = json.dumps({"event": "charge.success", "data": {"reference": "ZPAY1"}}).encode()
        self.assertFalse(kora.verify_webhook(raw, ""))


class KoraWebhookMockModeTests(SimpleTestCase):
    def test_accepts_in_tests_without_keys(self):
        # No key => mock mode; verify_webhook accepts so local webhook testing works.
        raw = json.dumps({"event": "charge.success", "data": {"reference": "ZPAY1"}}).encode()
        self.assertTrue(kora.verify_webhook(raw, "anything"))
