"""Tests for the Kora (Korapay) provider (utility.kora).

Two layers, neither needing real Kora credentials:

- MOCK mode (no key): functions short-circuit to a success stub, proving the
  offline seam matches the rest of the provider layer.
- Simulated LIVE mode: ``kora_live`` + ``requests`` are patched so the function
  builds a real request against Kora's documented ``{status, message, data}``
  envelope and parses it — proving the wiring without hitting the network.
  (Endpoint paths themselves are still marked VERIFY-BEFORE-LIVE in kora.py.)

Also covers the KYC provider seam: KYC_PROVIDER="kora" routes verify_bvn /
verify_nin through Kora.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from utility import kora as K
from utility import providers as P


def _resp(body, ok=True):
    m = MagicMock()
    m.ok = ok
    m.content = b"x"
    m.json.return_value = body
    return m


class KoraMockTests(SimpleTestCase):
    """No key configured -> functions return their mock success stub."""

    def test_mock_mode_is_active(self):
        self.assertFalse(K.kora_live())

    def test_payment_initialize_mock(self):
        r = K.payment_initialize("ada@example.com", 5000, "ZB-FUND-1")
        self.assertTrue(r["success"])
        self.assertTrue(r["authorization_url"].startswith("mock://kora/checkout/"))

    def test_create_virtual_account_mock(self):
        r = K.create_virtual_account("usr-1", "ADA EZE", "ada@example.com", "ADA EZE", bvn="22212345678")
        self.assertTrue(r["success"])
        self.assertTrue(r["account_number"].startswith("88"))

    def test_resolve_account_mock(self):
        self.assertEqual(K.resolve_account("0123456789", "058")["name"], "ADEYEMI WILLIAM")

    def test_disburse_mock(self):
        self.assertTrue(K.disburse(1000, "ZB-PO-1", "test", "058", "0123456789", "ADA EZE")["success"])

    def test_identity_length_validated_before_call(self):
        self.assertEqual(K.verify_bvn("123")["message"], "BVN must be 11 digits")
        self.assertEqual(K.verify_nin("xx")["message"], "NIN must be 11 digits")
        self.assertEqual(K.verify_vnin("short")["message"], "Virtual NIN must be 16 characters")

    def test_identity_mock_succeeds(self):
        self.assertTrue(K.verify_bvn("22212345678")["success"])
        self.assertTrue(K.verify_nin("98765432109")["success"])
        self.assertTrue(K.verify_vnin("YA1234567890ABCD")["success"])

    def test_card_issue_mock(self):
        ch = K.create_cardholder("ADA EZE", "ada@example.com")
        self.assertTrue(ch["success"])
        card = K.create_card(ch["reference"])
        self.assertTrue(card["success"])
        self.assertTrue(card["card_token"].startswith("card_mock_"))

    def test_webhook_accepts_in_dev_when_keyless(self):
        # DEBUG/TESTING => mock_disabled_in_prod() is False => accept.
        self.assertTrue(K.verify_webhook({"data": {"reference": "x"}}, "anything"))


@override_settings(
    KORA={"BASE_URL": "https://api.korapay.com/merchant", "SECRET_KEY": "sk_test_x", "PUBLIC_KEY": ""}
)
class KoraLiveTests(SimpleTestCase):
    """Key 'present' -> functions build the real request and parse the envelope."""

    def test_kora_live_true(self):
        self.assertTrue(K.kora_live())

    @patch("utility.kora.requests.post")
    def test_payment_initialize_live(self, mock_post):
        mock_post.return_value = _resp({"status": True, "message": "ok",
                                        "data": {"checkout_url": "https://checkout.korapay.com/abc",
                                                 "reference": "ZB-FUND-1"}})
        r = K.payment_initialize("ada@example.com", 5000, "ZB-FUND-1")
        self.assertTrue(r["success"])
        self.assertEqual(r["authorization_url"], "https://checkout.korapay.com/abc")
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertTrue(url.endswith("/api/v1/charges/initialize"))
        self.assertEqual(kwargs["json"]["reference"], "ZB-FUND-1")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk_test_x")

    @patch("utility.kora.requests.post")
    def test_create_virtual_account_live_threads_bvn(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {
            "account_number": "7650000006", "bank_name": "Wema Bank",
            "account_name": "ADA EZE", "account_reference": "usr-1"}})
        r = K.create_virtual_account("usr-1", "ADA EZE", "ada@example.com", "ADA EZE", bvn="22212345678")
        self.assertTrue(r["success"])
        self.assertEqual(r["account_number"], "7650000006")
        self.assertEqual(mock_post.call_args[1]["json"]["kyc"]["bvn"], "22212345678")

    @patch("utility.kora.requests.post")
    def test_resolve_account_live(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"account_name": "ADA EZE"}})
        r = K.resolve_account("0123456789", "058")
        self.assertTrue(r["success"])
        self.assertEqual(r["name"], "ADA EZE")
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body, {"bank": "058", "account": "0123456789"})

    @patch("utility.kora.requests.post")
    def test_disburse_live_accepts_processing(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"status": "processing",
                                                                 "reference": "ZB-PO-1"}})
        r = K.disburse(1000, "ZB-PO-1", "salary", "058", "0123456789", "ADA EZE")
        self.assertTrue(r["success"])
        self.assertEqual(r["status"], "processing")
        dest = mock_post.call_args[1]["json"]["destination"]
        self.assertEqual(dest["bank_account"], {"bank": "058", "account": "0123456789"})

    @patch("utility.kora.requests.get")
    def test_verify_payout_pending(self, mock_get):
        mock_get.return_value = _resp({"status": True, "data": {"status": "processing"}})
        r = K.verify_payout("ZB-PO-1")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])

    @patch("utility.kora.requests.post")
    def test_bvn_live(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"first_name": "ADA", "last_name": "EZE"}})
        r = K.verify_bvn("22212345678")
        self.assertTrue(r["success"])
        self.assertEqual(r["first_name"], "ADA")
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/v1/identities/ng/bvn"))
        self.assertEqual(mock_post.call_args[1]["json"], {"id": "22212345678"})

    @patch("utility.kora.requests.post")
    def test_vnin_live(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"first_name": "ADA"}})
        r = K.verify_vnin("YA1234567890ABCD")
        self.assertTrue(r["success"])
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/v1/identities/ng/vnin"))

    @patch("utility.kora.requests.get")
    def test_diagnostics_ok(self, mock_get):
        # balances read (auth) + then resolve uses POST; patch GET for balances.
        mock_get.return_value = _resp({"status": True, "data": {"NGN": {"available_balance": 100}}})
        with patch("utility.kora.requests.post") as mock_post:
            mock_post.return_value = _resp({"status": True, "data": {"account_name": "ADA EZE"}})
            d = K.kora_diagnostics()
        self.assertEqual(d["status"], "ok")
        self.assertTrue(d["auth_ok"])

    def test_webhook_signature_roundtrip(self):
        payload = {"event": "charge.success", "data": {"reference": "ZB-FUND-1", "amount": "5000"}}
        encoded = json.dumps(payload["data"], separators=(",", ":")).encode()
        sig = hmac.new(b"sk_test_x", encoded, hashlib.sha256).hexdigest()
        self.assertTrue(K.verify_webhook(payload, sig))
        self.assertFalse(K.verify_webhook(payload, "deadbeef"))
        self.assertFalse(K.verify_webhook(payload, ""))


@override_settings(
    KYC_PROVIDER="kora",
    KORA={"BASE_URL": "https://api.korapay.com/merchant", "SECRET_KEY": "sk_test_x", "PUBLIC_KEY": ""},
)
class KoraKycSeamTests(SimpleTestCase):
    """KYC_PROVIDER='kora' routes the provider-agnostic entry points to Kora."""

    def test_provider_selected(self):
        self.assertEqual(P.kyc_provider(), "kora")

    @patch("utility.kora.requests.post")
    def test_verify_bvn_routes_to_kora(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"first_name": "ADA", "last_name": "EZE"}})
        r = P.verify_bvn("22212345678")
        self.assertTrue(r["success"])
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/v1/identities/ng/bvn"))

    @patch("utility.kora.requests.post")
    def test_verify_nin_routes_to_kora(self, mock_post):
        mock_post.return_value = _resp({"status": True, "data": {"first_name": "ADA"}})
        r = P.verify_nin("98765432109")
        self.assertTrue(r["success"])
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/v1/identities/ng/nin"))
