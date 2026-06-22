"""Tests for the Monnify-backed KYC (VAS) and Bills Payment provider functions.

Two layers, neither of which needs real Monnify credentials:

- MOCK mode (no keys): the functions short-circuit to a success stub, proving
  the offline seam matches the rest of providers.py.
- Simulated LIVE mode: ``requests`` + ``_monnify_token`` + ``payments_live`` are
  patched so the function builds a real request and parses Monnify's documented
  ``{requestSuccessful, responseBody}`` envelope — proving the wiring is correct
  without hitting the network. (The live endpoint paths themselves are still
  marked VERIFY-BEFORE-LIVE in providers.py.)
"""
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from utility import providers as P


def _resp(body, ok=True):
    m = MagicMock()
    m.ok = ok
    m.json.return_value = body
    return m


class MonnifyKycBillsMockTests(SimpleTestCase):
    """No keys configured -> every function returns its mock success stub."""

    def test_mock_mode_is_active(self):
        self.assertFalse(P.payments_live())

    # --- KYC / VAS ---
    def test_bvn_mock_succeeds(self):
        r = P.monnify_verify_bvn("22212345678", name="ADA EZE")
        self.assertTrue(r["success"])
        self.assertTrue(r["mock"])

    def test_bvn_length_is_validated_before_any_call(self):
        r = P.monnify_verify_bvn("123")
        self.assertFalse(r["success"])
        self.assertEqual(r["message"], "BVN must be 11 digits")

    def test_bvn_account_match_mock_succeeds(self):
        r = P.monnify_match_bvn_account("22212345678", "058", "0123456789")
        self.assertTrue(r["success"])
        self.assertTrue(r["matched"])

    def test_nin_mock_succeeds(self):
        r = P.monnify_verify_nin("98765432109")
        self.assertTrue(r["success"])

    def test_nin_length_is_validated(self):
        r = P.monnify_verify_nin("abc")
        self.assertFalse(r["success"])
        self.assertEqual(r["message"], "NIN must be 11 digits")

    # --- bills ---
    def test_bill_categories_mock(self):
        r = P.monnify_bill_categories()
        self.assertTrue(r["success"])
        self.assertIn("ELECTRICITY", r["responseBody"])

    def test_billers_and_products_mock(self):
        self.assertTrue(P.monnify_billers("ELECTRICITY")["success"])
        self.assertTrue(P.monnify_biller_products("MOCK-BILLER")["success"])

    def test_validate_customer_mock_returns_name(self):
        r = P.monnify_validate_customer("MOCK-PROD", "45010101010")
        self.assertTrue(r["success"])
        self.assertEqual(r["customer_name"], "ADEYEMI WILLIAM")

    def test_pay_bill_mock_succeeds_with_reference(self):
        r = P.monnify_pay_bill("MOCK-PROD", "08010000001", 1000, "ZB-REF-1")
        self.assertTrue(r["success"])
        self.assertTrue(r["provider_reference"].startswith("MOCK-"))

    def test_bill_status_mock(self):
        self.assertTrue(P.monnify_bill_status("ZB-REF-1")["success"])


@patch("utility.providers._monnify_token", return_value="tok")
@patch("utility.providers.payments_live", return_value=True)
class MonnifyKycBillsLiveTests(SimpleTestCase):
    """Keys 'present' (patched) -> functions build the real request and parse
    Monnify's envelope. Patches are passed bottom-up: requests.* first."""

    @patch("utility.providers.requests.post")
    def test_bvn_live_builds_request_and_parses_match(self, mock_post, *_):
        mock_post.return_value = _resp({
            "requestSuccessful": True, "responseMessage": "success",
            "responseBody": {"bvn": "22212345678",
                             "bvnInformationMatch": {"name": "FULL_MATCH", "mobileNo": "NO_MATCH"}},
        })
        r = P.monnify_verify_bvn("22212345678", name="ADA EZE", mobile="08010000001")
        self.assertTrue(r["success"])
        self.assertEqual(r["match"]["name"], "FULL_MATCH")
        url, kwargs = mock_post.call_args[0][0], mock_post.call_args[1]
        self.assertTrue(url.endswith("/api/v1/vas/bvn-details-match"))
        self.assertEqual(kwargs["json"]["bvn"], "22212345678")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

    @patch("utility.providers.requests.post")
    def test_nin_live(self, mock_post, *_):
        mock_post.return_value = _resp({
            "requestSuccessful": True, "responseBody": {"firstName": "ADA", "lastName": "EZE"}})
        r = P.monnify_verify_nin("98765432109")
        self.assertTrue(r["success"])
        self.assertTrue(mock_post.call_args[0][0].endswith("/api/v1/vas/nin-details"))
        self.assertEqual(mock_post.call_args[1]["json"]["nin"], "98765432109")

    @patch("utility.providers.requests.get")
    def test_categories_live(self, mock_get, *_):
        mock_get.return_value = _resp({
            "requestSuccessful": True, "responseBody": ["AIRTIME", "ELECTRICITY"]})
        r = P.monnify_bill_categories()
        self.assertTrue(r["success"])
        self.assertEqual(r["responseBody"], ["AIRTIME", "ELECTRICITY"])
        self.assertIn("/api/v1/bill-payment/biller-categories", mock_get.call_args[0][0])

    @patch("utility.providers.requests.post")
    def test_validate_customer_live_flags_validation_ref(self, mock_post, *_):
        mock_post.return_value = _resp({
            "requestSuccessful": True,
            "responseBody": {"customerName": "ADEYEMI WILLIAM",
                             "vendInstruction": {"requireValidationRef": True,
                                                 "validationReference": "VR-123"}},
        })
        r = P.monnify_validate_customer("DSTV-PADI", "1234567890")
        self.assertTrue(r["success"])
        self.assertEqual(r["customer_name"], "ADEYEMI WILLIAM")
        self.assertTrue(r["requires_validation_ref"])
        self.assertEqual(r["validation_reference"], "VR-123")

    @patch("utility.providers.requests.post")
    def test_pay_bill_live_success_threads_validation_ref(self, mock_post, *_):
        mock_post.return_value = _resp({
            "requestSuccessful": True,
            "responseBody": {"status": "SUCCESS", "transactionReference": "MNFY-TX-9",
                             "token": "1234-5678-9012"},
        })
        r = P.monnify_pay_bill("IKEDC-PREPAID", "45010101010", 5000, "ZB-ELEC-1",
                               validation_reference="VR-123")
        self.assertTrue(r["success"])
        self.assertEqual(r["provider_reference"], "MNFY-TX-9")
        self.assertEqual(r["token"], "1234-5678-9012")
        body = mock_post.call_args[1]["json"]
        self.assertEqual(body["reference"], "ZB-ELEC-1")          # idempotency key threaded
        self.assertEqual(body["validationReference"], "VR-123")   # only present when supplied

    @patch("utility.providers.requests.post")
    def test_pay_bill_accepted_but_pending_is_pending_not_success(self, mock_post, *_):
        """An accepted vend that Monnify reports PENDING must NOT settle as success:
        settle_or_refund would mark the debit Successful and stop reconciling a vend
        that has not yet delivered. It must return pending so the caller requeries."""
        mock_post.return_value = _resp({
            "requestSuccessful": True,
            "responseBody": {"status": "PENDING", "transactionReference": "MNFY-TX-P"},
        })
        r = P.monnify_pay_bill("PROD", "0810", 1000, "ZB-REF-P")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])
        self.assertEqual(r["status"], "PENDING")

    @patch("utility.providers.requests.post")
    def test_pay_bill_rejected_is_failed_not_pending(self, mock_post, *_):
        """An outright rejection (requestSuccessful=False) means the vend never
        entered Monnify's queue, so it is a definitive failure the caller refunds —
        not a pending the caller would reconcile forever."""
        mock_post.return_value = _resp({
            "requestSuccessful": False, "responseMessage": "Invalid product",
            "responseBody": {},
        })
        r = P.monnify_pay_bill("BAD-PROD", "0810", 1000, "ZB-REF-R")
        self.assertFalse(r["success"])
        self.assertFalse(r.get("pending", False))

    @patch("utility.providers.requests.post", side_effect=requests.RequestException("down"))
    def test_pay_bill_network_error_is_pending_not_failed(self, *_):
        """A timeout must not look like a definitive failure — the vend may have
        landed, so the caller must reconcile (requery), never blind-refund."""
        r = P.monnify_pay_bill("PROD", "0810", 1000, "ZB-REF-X")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])

    @patch("utility.providers.requests.get")
    def test_bill_status_pending_stays_pending(self, mock_get, *_):
        mock_get.return_value = _resp({
            "requestSuccessful": True, "responseBody": {"status": "PENDING"}})
        r = P.monnify_bill_status("ZB-REF-X")
        self.assertFalse(r["success"])
        self.assertTrue(r["pending"])

    @patch("utility.providers.requests.get")
    def test_bill_status_delivered_succeeds(self, mock_get, *_):
        mock_get.return_value = _resp({
            "requestSuccessful": True, "responseBody": {"status": "DELIVERED"}})
        r = P.monnify_bill_status("ZB-REF-Y")
        self.assertTrue(r["success"])


class KycProviderDispatchTests(SimpleTestCase):
    """verify_bvn / verify_nin route to the provider KYC_PROVIDER selects, and the
    accounts/wallet views call these (not a hard-wired provider)."""

    def test_default_unconfigured_is_monnify(self):
        # No keys + no explicit setting -> Monnify (the app's primary rail).
        self.assertEqual(P.kyc_provider(), "monnify")

    @override_settings(KYC_PROVIDER="monnify")
    def test_verify_bvn_routes_to_monnify(self):
        with patch("utility.providers.monnify_verify_bvn", return_value={"success": True, "via": "monnify"}) as mv, \
                patch("utility.providers.kyc_verify_bvn") as pv:
            out = P.verify_bvn("22212345678", name="ADA EZE", mobile="08010000001")
        self.assertEqual(out["via"], "monnify")
        mv.assert_called_once()
        pv.assert_not_called()

    @override_settings(KYC_PROVIDER="prembly")
    def test_verify_bvn_routes_to_prembly(self):
        with patch("utility.providers.kyc_verify_bvn", return_value={"success": True, "via": "prembly"}) as pv, \
                patch("utility.providers.monnify_verify_bvn") as mv:
            out = P.verify_bvn("22212345678")
        self.assertEqual(out["via"], "prembly")
        pv.assert_called_once()
        mv.assert_not_called()

    @override_settings(KYC_PROVIDER="prembly")
    def test_verify_nin_routes_to_prembly(self):
        with patch("utility.providers.kyc_verify_nin", return_value={"success": True, "via": "prembly"}) as pv, \
                patch("utility.providers.monnify_verify_nin") as mv:
            out = P.verify_nin("98765432109")
        self.assertEqual(out["via"], "prembly")
        pv.assert_called_once()
        mv.assert_not_called()
