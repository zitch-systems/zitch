"""Tests for the payment/payout/KYC/card provider-selection dispatch layer
(utility.providers).

Wema/ALAT is the sole money-movement + Nigeria-KYC rail; the funding_* / payout_* /
verify_* wrappers delegate to it. VAS stays on VTU.ng (Wema airtime opt-in) and
virtual cards on the generic issuer. Pure routing (SimpleTestCase): no network.
"""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from utility import providers as P

WEMA_LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
             "KEYS": {"wallet": "subkey"}, "SOURCE_ACCOUNT": "0100000001",
             "SECURITY_INFO": "sec", "SIMULATION": False}


class ProviderSelectionTests(SimpleTestCase):
    """Wema is the sole money-movement rail."""

    def test_money_rail_is_wema(self):
        self.assertEqual(P.payment_provider(), "wema")
        self.assertEqual(P.payout_provider(), "wema")

    @override_settings(PAYMENT_PROVIDER="kora", PAYOUT_PROVIDER="monnify")
    def test_removed_choice_falls_back_to_wema(self):
        # A legacy/unknown *_PROVIDER value falls back to the sole rail.
        self.assertEqual(P.payment_provider(), "wema")
        self.assertEqual(P.payout_provider(), "wema")

    def test_card_provider_defaults_to_issuer(self):
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(CARD_PROVIDER="wema")
    def test_card_provider_ignores_unwired_wema(self):
        # Wema card issuing is not integrated — the selector falls back to issuer.
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(WEMA=WEMA_LIVE)
    def test_payout_live_tracks_wema_keys(self):
        self.assertTrue(P.payout_live())

    def test_payout_live_false_without_keys(self):
        self.assertFalse(P.payout_live())


class FundingDispatchTests(SimpleTestCase):
    """Wema funds by an OTP-provisioned NUBAN — no hosted checkout, no sync reserve."""

    def test_funding_initialize_returns_transfer_message(self):
        out = P.funding_initialize("a@b.com", 1000, "ZPAY1", name="Ada")
        self.assertFalse(out["success"])
        self.assertIn("transfer", out["message"].lower())

    def test_funding_verify_is_automatic(self):
        out = P.funding_verify("ZPAY1")
        self.assertFalse(out["success"])

    def test_funding_account_reserve_signals_otp(self):
        out = P.funding_account_reserve("usr-1", "Ada", "a@b.com", "Ada", bvn="22212345678")
        self.assertFalse(out["success"])
        self.assertTrue(out["otp_required"])

    def test_funding_account_get_signals_otp(self):
        out = P.funding_account_get("ZITCH-WALLET-1")
        self.assertFalse(out["success"])
        self.assertTrue(out["otp_required"])


class PayoutDispatchTests(SimpleTestCase):
    @override_settings(WEMA=WEMA_LIVE)
    def test_payout_send_routes_to_wema_with_source_and_bank_name(self):
        with patch("utility.wema.transfer",
                   return_value={"success": True, "status": "SUCCESS"}) as m:
            P.payout_send(1000, "ZTRF1", "note", "035", "0123456789", "ADA EZE", bank_name="Wema Bank")
        m.assert_called_once()
        kw = m.call_args.kwargs
        self.assertEqual(kw["source_account"], "0100000001")       # from WEMA_SOURCE_ACCOUNT
        self.assertEqual(kw["destination_account"], "0123456789")
        self.assertEqual(kw["destination_bank_code"], "035")
        self.assertEqual(kw["destination_bank_name"], "Wema Bank")
        self.assertEqual(kw["destination_name"], "ADA EZE")

    def test_payout_resolve_routes_to_wema(self):
        with patch("utility.wema.resolve_account",
                   return_value={"success": True, "name": "ADA EZE"}) as m:
            P.payout_resolve_account("0123456789", "035")
        m.assert_called_once_with("0123456789", "035")

    @override_settings(WEMA={**WEMA_LIVE, "SOURCE_ACCOUNT": ""})
    def test_payout_send_fails_closed_without_source_account(self):
        # Live Wema payout with no sender NUBAN and no pool must refuse (refundable)
        # rather than send an empty sourceAccountNumber. wema.transfer must NOT be called.
        with patch("utility.wema.transfer") as m:
            out = P.payout_send(1000, "ZTRF1", "note", "035", "0123456789", "ADA EZE", bank_name="Wema Bank")
        self.assertFalse(out["success"])
        m.assert_not_called()

    @override_settings(WEMA=WEMA_LIVE)
    def test_payout_send_prefers_sender_nuban_over_pool(self):
        # Per-user-balance model: debit the SENDER's own NUBAN, not the pool.
        with patch("utility.wema.transfer", return_value={"success": True, "status": "SUCCESS"}) as m:
            P.payout_send(1000, "ZTRF1", "note", "035", "0123456789", "ADA EZE",
                          bank_name="Wema Bank", source_account="0199999999")
        self.assertEqual(m.call_args.kwargs["source_account"], "0199999999")  # sender NUBAN wins over pool


class KycDispatchTests(SimpleTestCase):
    """BVN/NIN/vNIN all on Wema Full KYC (the sole identity-lookup rail)."""

    def test_kyc_provider_is_wema(self):
        self.assertEqual(P.kyc_provider(), "wema")

    @override_settings(KYC_PROVIDER="kora")
    def test_removed_choice_falls_back_to_wema(self):
        self.assertEqual(P.kyc_provider(), "wema")

    def test_verify_bvn_routes_to_wema(self):
        with patch("utility.wema.verify_bvn", return_value={"success": True}) as mm:
            P.verify_bvn("22222222222", name="Ada Eze", mobile="080")
        mm.assert_called_once()
        self.assertEqual(mm.call_args.kwargs["name"], "Ada Eze")

    def test_verify_nin_routes_to_wema(self):
        with patch("utility.wema.verify_nin", return_value={"success": True}) as mm:
            P.verify_nin("12345678901")
        mm.assert_called_once_with("12345678901")

    def test_verify_vnin_routes_to_wema(self):
        with patch("utility.wema.verify_vnin", return_value={"success": True}) as mk:
            P.verify_vnin("AB123456789CDEFG")
        mk.assert_called_once()


class VasDispatchTests(SimpleTestCase):
    def test_vas_provider_defaults_to_vtung(self):
        self.assertEqual(P.vas_provider(), "vtung")

    @override_settings(VAS_PROVIDER="wema")
    def test_airtime_routes_to_wema(self):
        with patch("utility.wema.purchase_airtime", return_value={"success": True}) as mw, \
             patch("utility.vtung.vt_purchase") as mv:
            P.vtu_purchase("mtn-airtime", {"amount": "500", "phone": "080", "source_account": "0155500011"},
                           reference="R")
        mw.assert_called_once()
        self.assertEqual(mw.call_args.kwargs["source_account"], "0155500011")  # sender NUBAN threaded
        mv.assert_not_called()

    @override_settings(VAS_PROVIDER="wema")
    def test_data_stays_on_vtung(self):
        # Data needs Wema's catalog — must NOT route to Wema yet.
        with patch("utility.vtung.vt_purchase", return_value={"success": True}) as mv, \
             patch("utility.wema.purchase_airtime") as mw:
            P.vtu_purchase("mtn-data", {"amount": "500", "phone": "080"}, reference="R")
        mv.assert_called_once()
        mw.assert_not_called()

    def test_airtime_stays_on_vtung_by_default(self):
        with patch("utility.vtung.vt_purchase", return_value={"success": True}) as mv:
            P.vtu_purchase("mtn-airtime", {"amount": "500", "phone": "080"}, reference="R")
        mv.assert_called_once()


class CardDispatchTests(SimpleTestCase):
    def test_card_issue_routes_to_generic_issuer(self):
        # Wema card issuing is not integrated — card_issue delegates to CARD_ISSUER.
        with patch("utility.providers.issue_card",
                   return_value={"success": True, "card_token": "card_1"}) as m:
            out = P.card_issue("ADA EZE", "42", email="ada@b.com")
        m.assert_called_once()
        self.assertTrue(out["success"])
