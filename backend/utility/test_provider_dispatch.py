"""Tests for the payment/payout/KYC/card provider-selection dispatch layer
(utility.providers).

Wema/ALAT is the sole money-movement + Nigeria-KYC rail; the funding_* / payout_* /
verify_* wrappers delegate to it. VAS routes per-service to Wema once its keys +
catalogue are in place (airtime always; data/cable when synced; electricity/betting
stay on VTU.ng); virtual cards on the generic issuer.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

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
    def test_card_provider_explicit_wema(self):
        # Wema Virtual Naira Card is wired — an explicit choice is honoured.
        self.assertEqual(P.card_provider(), "wema")

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
            P.verify_nin("12345678901", name="Ada Eze")
        mm.assert_called_once_with("12345678901", name="Ada Eze")

    def test_verify_vnin_routes_to_wema(self):
        with patch("utility.wema.verify_vnin", return_value={"success": True}) as mk:
            P.verify_vnin("AB123456789CDEFG", name="Ada Eze")
        mk.assert_called_once_with("AB123456789CDEFG", name="Ada Eze")


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
    def test_card_issue_routes_to_generic_issuer_by_default(self):
        # No Wema card key configured -> card_provider() is 'issuer'.
        with patch("utility.providers.issue_card",
                   return_value={"success": True, "card_token": "card_1"}) as m:
            out = P.card_issue("ADA EZE", "42", email="ada@b.com")
        m.assert_called_once()
        self.assertTrue(out["success"])

    @override_settings(CARD_PROVIDER="wema")
    def test_card_issue_routes_to_wema_when_selected(self):
        # Wema keys the card by the user's NUBAN — the account number is threaded through.
        with patch("utility.wema.card_issue",
                   return_value={"success": True, "card_token": "0155500011", "last4": "1234"}) as m:
            out = P.card_issue("ADA EZE", "42", email="ada@b.com", account_number="0155500011")
        m.assert_called_once()
        self.assertEqual(m.call_args.args, ("ADA EZE", "42"))
        self.assertEqual(m.call_args.kwargs["account_number"], "0155500011")
        self.assertTrue(out["success"])

    @override_settings(CARD_PROVIDER="wema")
    def test_card_freeze_fund_reveal_route_to_wema(self):
        with patch("utility.wema.card_set_status", return_value={"success": True}) as ms, \
             patch("utility.wema.card_fund", return_value={"success": True}) as mf, \
             patch("utility.wema.card_reveal", return_value={"success": True, "pan": "5061", "cvv": "123"}) as mr:
            P.card_set_status("wema_1", active=False)
            P.card_fund("wema_1", 5000)
            P.card_reveal("wema_1")
        ms.assert_called_once_with("wema_1", False)
        mf.assert_called_once_with("wema_1", 5000)
        mr.assert_called_once_with("wema_1")


class WemaVasRoutingTests(TestCase):
    """DB-backed VAS routing: data/cable go to Wema only once wema_code is synced,
    and a PENDING purchase requeries against the rail stamped on the ledger row."""

    @override_settings(VAS_PROVIDER="wema")
    def test_data_routes_to_wema_when_code_synced(self):
        from utility.models import DataPlan
        DataPlan.objects.create(network="1", plan_type="1", name="1GB", validity="30 days",
                                plan_code="MTN1GB", wema_code="WEMA-MTN-1GB", price=Decimal("500"))
        with patch("utility.wema.purchase_data", return_value={"success": True, "status": "SUCCESS"}) as mw, \
             patch("utility.vtung.vt_purchase") as mv:
            out = P.vtu_purchase("mtn-data", {"variation_code": "MTN1GB", "phone": "080"}, reference="R")
        mw.assert_called_once()
        self.assertEqual(mw.call_args.args[4], "WEMA-MTN-1GB")  # package_code positional
        self.assertEqual(out["vas_rail"], "wema")
        mv.assert_not_called()

    @override_settings(VAS_PROVIDER="wema")
    def test_data_stays_on_vtung_without_wema_code(self):
        from utility.models import DataPlan
        DataPlan.objects.create(network="1", plan_type="1", name="2GB", validity="30 days",
                                plan_code="MTN2GB", wema_code="", price=Decimal("1000"))
        with patch("utility.vtung.vt_purchase", return_value={"success": True}) as mv, \
             patch("utility.wema.purchase_data") as mw:
            P.vtu_purchase("mtn-data", {"variation_code": "MTN2GB", "phone": "080"}, reference="R")
        mv.assert_called_once()
        mw.assert_not_called()

    @override_settings(VAS_PROVIDER="wema")
    def test_cable_routes_to_wema_when_code_synced(self):
        from utility.models import CablePlan
        CablePlan.objects.create(provider="2", name="DStv Compact", cable_plan_code="DSTV-C",
                                 wema_code="WEMA-DSTV-C", price=Decimal("10500"))
        with patch("utility.wema.pay_bill", return_value={"success": True, "status": "SUCCESS"}) as mw, \
             patch("utility.vtung.vt_purchase") as mv:
            out = P.vtu_purchase("dstv", {"variation_code": "DSTV-C", "billersCode": "1234567890"}, reference="R")
        mw.assert_called_once()
        self.assertEqual(mw.call_args.kwargs["package_id"], "WEMA-DSTV-C")
        self.assertEqual(out["vas_rail"], "wema")
        mv.assert_not_called()

    @override_settings(VAS_PROVIDER="wema")
    def test_data_purchase_debits_buyer_nuban_from_ledger_reference(self):
        # No explicit source_account in the payload (the app data/cable views and
        # the WhatsApp router don't send one): the buyer's own wallet NUBAN must
        # be resolved from the ledger row, per the per-user-balance money-flow
        # model — NOT silently fall back to the shared pool account.
        from utility.models import DataPlan
        from wallet.services import debit
        from wallet.tests import make_user
        DataPlan.objects.create(network="1", plan_type="1", name="3GB", validity="30 days",
                                plan_code="MTN3GB", wema_code="WEMA-MTN-3GB", price=Decimal("1500"))
        user, _ = make_user("08033330002", "nuban@zitch.test", balance="5000")
        wallet = user.wallet
        wallet.account_number = "0155500099"
        wallet.save(update_fields=["account_number"])
        txn = debit(user, Decimal("1500"), "Data — MTN 3GB", meta={})
        with patch("utility.wema.purchase_data",
                   return_value={"success": True, "status": "SUCCESS"}) as mw:
            P.vtu_purchase("mtn-data", {"variation_code": "MTN3GB", "phone": "080"},
                           reference=txn.reference)
        self.assertEqual(mw.call_args.kwargs["source_account"], "0155500099")

    @override_settings(VAS_PROVIDER="wema")
    def test_explicit_source_account_wins_over_ledger_lookup(self):
        with patch("utility.wema.purchase_airtime", return_value={"success": True}) as mw:
            P.vtu_purchase("mtn-airtime",
                           {"amount": "500", "phone": "080", "source_account": "0100000042"},
                           reference="NO-SUCH-LEDGER-ROW")
        self.assertEqual(mw.call_args.kwargs["source_account"], "0100000042")

    def test_requery_uses_stamped_wema_rail(self):
        from wallet.services import debit
        from wallet.tests import make_user
        user, _ = make_user("08033330001", "vas@zitch.test", balance="1000")
        txn = debit(user, Decimal("500"), "Airtime", meta={"vas_rail": "wema", "vas_type": "airtime"})
        with patch("utility.wema.vas_status", return_value={"success": True}) as mw, \
             patch("utility.vtung.vt_requery") as mv:
            P.vtu_requery(txn.reference)
        mw.assert_called_once_with(txn.reference, "airtime")
        mv.assert_not_called()

    def test_requery_defaults_to_vtung(self):
        with patch("utility.vtung.vt_requery", return_value={"success": True}) as mv, \
             patch("utility.wema.vas_status") as mw:
            P.vtu_requery("NO-SUCH-REF")
        mv.assert_called_once()
        mw.assert_not_called()


PREMBLY_LIVE = {"BASE_URL": "https://api.prembly.com", "API_KEY": "k", "APP_ID": "a"}
PREMBLY_OFF = {"BASE_URL": "https://api.prembly.com", "API_KEY": "", "APP_ID": ""}


class NinIdentityRailTests(SimpleTestCase):
    """NIN is the one identity the NUBAN flow cannot verify — it name-matches
    exactly ONE, in practice the BVN that opened the account. Without a second
    rail every NIN falls to the operator review queue and nobody can spend until
    a human clears them. Prembly has the standalone lookup; these pin down that
    it is used, and that it fails CLOSED, because this result lifts a tier."""

    def _resp(self, payload, status=200):
        class R:
            status_code = status

            def json(self):
                return payload
        return R()

    @override_settings(PREMBLY=PREMBLY_OFF)
    def test_without_prembly_it_falls_back_to_the_previous_behaviour(self):
        with patch("utility.wema.verify_nin", return_value={"success": True, "mock": True}) as wema:
            self.assertTrue(P.verify_nin("12345678901", name="ADA EZE")["success"])
        wema.assert_called_once()

    @override_settings(PREMBLY=PREMBLY_LIVE)
    def test_a_matching_record_verifies(self):
        payload = {"status": True, "data": {"firstname": "ADA", "surname": "EZE"}}
        with patch("utility.providers.requests.post", return_value=self._resp(payload)):
            result = P.verify_nin("12345678901", name="Ada Eze")
        self.assertTrue(result["success"])
        self.assertEqual((result["first_name"], result["last_name"]), ("ADA", "EZE"))

    @override_settings(PREMBLY=PREMBLY_LIVE)
    def test_a_different_person_is_refused_without_naming_them(self):
        """The resolved name belongs to whoever owns the NIN, who may not be the
        person asking — refusing must not tell the asker who they hit."""
        payload = {"status": True, "data": {"firstname": "CHINEDU", "surname": "OKAFOR"}}
        with patch("utility.providers.requests.post", return_value=self._resp(payload)):
            result = P.verify_nin("12345678901", name="Ada Eze")
        self.assertFalse(result["success"])
        self.assertNotIn("CHINEDU", result["message"])
        self.assertNotIn("OKAFOR", result["message"])

    @override_settings(PREMBLY=PREMBLY_LIVE)
    def test_a_record_with_no_name_is_not_a_pass(self):
        payload = {"status": True, "data": {"dob": "1990-01-01"}}
        with patch("utility.providers.requests.post", return_value=self._resp(payload)):
            self.assertFalse(P.verify_nin("12345678901", name="Ada Eze")["success"])

    @override_settings(PREMBLY=PREMBLY_LIVE)
    def test_an_unreachable_or_malformed_provider_fails_closed(self):
        import requests as R

        for effect in (R.RequestException("down"), ValueError("not json")):
            with patch("utility.providers.requests.post", side_effect=effect):
                self.assertFalse(P.verify_nin("12345678901", name="Ada Eze")["success"])
        with patch("utility.providers.requests.post",
                   return_value=self._resp({"status": False, "message": "not found"})):
            self.assertFalse(P.verify_nin("12345678901", name="Ada Eze")["success"])

    @override_settings(PREMBLY=PREMBLY_LIVE)
    def test_a_malformed_nin_never_reaches_the_provider(self):
        with patch("utility.providers.requests.post") as post:
            self.assertFalse(P.verify_nin("123", name="Ada Eze")["success"])
        post.assert_not_called()
