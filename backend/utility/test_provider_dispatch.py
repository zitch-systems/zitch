"""Tests for the payment/payout/KYC/card provider-selection dispatch layer
(utility.providers).

The partner bank is the sole money-movement, Nigeria-KYC and VAS rail; virtual
cards stay on the generic issuer where configured.
"""
from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from utility import providers as P

WEMA_LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
             "KEYS": {"wallet": "subkey"}, "SOURCE_ACCOUNT": "0100000001",
             "SECURITY_INFO": "sec", "SIMULATION": False}

# The partner bank keyed for VAS specifically, i.e. the one shape where real
# airtime/data/bills calls are made and the status legend therefore decides whether a
# PROCESSING purchase could ever be settled (see providers.vas_can_settle).
_WEMA_VAS_KEYED = {**WEMA_LIVE,
                   "KEYS": {"wallet": "subkey", "airtime": "airkey", "bills": "billkey"}}


class ProviderSelectionTests(SimpleTestCase):
    """partner bank is the sole money-movement rail."""

    def test_money_rail_is_9psb(self):
        self.assertEqual(P.payment_provider(), "9psb")
        self.assertEqual(P.payout_provider(), "9psb")

    @override_settings(PAYMENT_PROVIDER="kora", PAYOUT_PROVIDER="monnify")
    def test_removed_choice_cannot_redirect_9psb(self):
        # A legacy/unknown *_PROVIDER value falls back to the sole rail.
        self.assertEqual(P.payment_provider(), "9psb")
        self.assertEqual(P.payout_provider(), "9psb")

    def test_card_provider_defaults_to_issuer(self):
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(CARD_PROVIDER="wema")
    def test_retired_card_provider_cannot_redirect_issuer(self):
        # partner bank Virtual Naira Card is wired — an explicit choice is honoured.
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(WEMA=WEMA_LIVE)
    def test_wema_keys_cannot_enable_9psb(self):
        self.assertFalse(P.payout_live())

    def test_payout_live_false_without_keys(self):
        self.assertFalse(P.payout_live())


class FundingDispatchTests(SimpleTestCase):
    """partner bank funds by an OTP-provisioned NUBAN — no hosted checkout, no sync reserve."""

    def test_funding_initialize_returns_transfer_message(self):
        out = P.funding_initialize("a@b.com", 1000, "ZPAY1", name="Ada")
        self.assertFalse(out["success"])
        self.assertIn("transfer", out["message"].lower())

    def test_funding_verify_is_automatic(self):
        out = P.funding_verify("ZPAY1")
        self.assertFalse(out["success"])









class CardDispatchTests(SimpleTestCase):
    def test_card_capabilities_match_backend_contracts(self):
        self.assertEqual(P.card_capabilities("wema"), {
            "can_fund": False,
            "can_unfreeze": False,
            "permanent_block": True,
        })
        self.assertEqual(P.card_capabilities("issuer"), {
            "can_fund": True,
            "can_unfreeze": True,
            "permanent_block": False,
        })

    def test_card_issue_is_unavailable_before_issuer_dispatch(self):
        with patch("utility.providers.issue_card") as issuer:
            out = P.card_issue("ADA EZE", "42", email="ada@example.test")
        issuer.assert_not_called()
        self.assertFalse(out["success"])
        self.assertFalse(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_fund_timeout_is_ambiguous_not_refundable(self):
        with patch("utility.providers.requests.post",
                   side_effect=P.requests.Timeout("response lost")):
            out = P.card_fund("card_1", 5000)

        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_fund_http_acceptance_without_terminal_status_is_pending(self):
        response = Mock(status_code=202, ok=True, content=b"{}")
        response.json.return_value = {}
        with patch("utility.providers.requests.post", return_value=response):
            out = P.card_fund("card_1", 5000)
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_fund_http_409_is_ambiguous_not_refundable(self):
        response = Mock(status_code=409, ok=False, content=b"{}")
        response.json.return_value = {"status": "duplicate", "message": "conflict"}
        with patch("utility.providers.requests.post", return_value=response):
            out = P.card_fund("card_1", 5000)
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_fund_explicit_terminal_success_is_success(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {"data": {"status": "completed", "id": "load_1"}}
        with patch("utility.providers.requests.post", return_value=response):
            out = P.card_fund("card_1", 5000)
        self.assertTrue(out["success"])
        self.assertFalse(out.get("pending", False))

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": False},
    )
    def test_generic_card_credentials_do_not_enable_unverified_live_contract(self):
        self.assertFalse(P._card_issuer_live())
        with patch("utility.providers.requests.post") as post:
            out = P.issue_card("ADA EZE", "CI-1")
        post.assert_not_called()
        self.assertTrue(out["success"])  # test environment uses the deliberate mock
        self.assertTrue(out["mock"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_issue_timeout_is_pending(self):
        with patch("utility.providers.requests.post",
                   side_effect=P.requests.Timeout("response lost")):
            out = P.issue_card("ADA EZE", "CI-1")
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_issue_bare_id_without_terminal_success_is_pending(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {"data": {"id": "card_1", "maskedPan": "50611234",
                                                "expiryMonth": "12", "expiryYear": "2029"}}
        with patch("utility.providers.requests.post", return_value=response):
            out = P.issue_card("ADA EZE", "CI-1")
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_issue_needs_explicit_success_and_card_evidence(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {
            "status": True,
            "data": {"id": "card_1", "status": "issued", "maskedPan": "50611234",
                     "expiryMonth": "12", "expiryYear": "2029"},
        }
        with patch("utility.providers.requests.post", return_value=response):
            out = P.issue_card("ADA EZE", "CI-1")
        self.assertTrue(out["success"])
        self.assertEqual(out["last4"], "1234")
        self.assertEqual(out["expiry"], "12/29")

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_issue_terminal_rejection_allows_failure(self):
        response = Mock(status_code=422, ok=False, content=b"{}")
        response.json.return_value = {"status": "rejected", "message": "Invalid customer"}
        with patch("utility.providers.requests.post", return_value=response):
            out = P.issue_card("ADA EZE", "CI-1")
        self.assertFalse(out["success"])
        self.assertFalse(out.get("pending", False))

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_status_timeout_is_pending(self):
        with patch("utility.providers.requests.put",
                   side_effect=P.requests.Timeout("response lost")):
            out = P.card_set_status("card_1", active=False)
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_status_bare_2xx_is_pending(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {}
        with patch("utility.providers.requests.put", return_value=response):
            out = P.card_set_status("card_1", active=False)
        self.assertFalse(out["success"])
        self.assertTrue(out["pending"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_status_needs_explicit_expected_state(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {"data": {"status": "blocked"}}
        with patch("utility.providers.requests.put", return_value=response):
            out = P.card_set_status("card_1", active=False)
        self.assertTrue(out["success"])

    @override_settings(
        CARD_PROVIDER="issuer",
        WEMA={"SIMULATION": False},
        CARD_ISSUER={"API_KEY": "issuer-key", "BASE_URL": "https://issuer.invalid",
                     "BRAND": "Verve", "LIVE_ENABLED": True},
    )
    def test_generic_card_status_explicit_false_is_terminal(self):
        response = Mock(status_code=200, ok=True, content=b"{}")
        response.json.return_value = {"success": False, "message": "not changed"}
        with patch("utility.providers.requests.put", return_value=response):
            out = P.card_set_status("card_1", active=False)
        self.assertFalse(out["success"])
        self.assertFalse(out.get("pending", False))




    @override_settings(CARD_PROVIDER="issuer")
    def test_cards_from_the_retired_issuer_never_reach_the_generic_issuer(self):
        with patch("utility.providers.set_card_status") as status, \
             patch("utility.providers.fund_card") as fund, \
             patch("utility.providers.card_secure_details") as reveal:
            results = (P.card_set_status("0155500011", active=False, provider="wema",
                                         masked_pan="5061******1234"),
                       P.card_fund("0155500011", 5000, provider="wema"),
                       P.card_reveal("0155500011", provider="wema"))
        for mock in (status, fund, reveal):
            mock.assert_not_called()
        for result in results:
            self.assertFalse(result["success"])
            self.assertEqual(result["code"], "provider_archived")




PREMBLY_LIVE = {"BASE_URL": "https://api.prembly.com", "API_KEY": "k", "APP_ID": "a"}
PREMBLY_OFF = {"BASE_URL": "https://api.prembly.com", "API_KEY": "", "APP_ID": ""}
