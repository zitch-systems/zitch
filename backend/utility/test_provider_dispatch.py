"""Tests for the payment/payout/card provider-selection dispatch layer and the
Monnify webhook endpoints (utility.providers dispatch + wallet/transfers views).

Two layers:
- Pure routing (SimpleTestCase): the *_provider() selectors and the funding_* /
  payout_* / card_* wrappers delegate to the Monnify client (Wema is opt-in).
- Webhook crediting (TestCase): the Monnify pay-in/payout webhooks credit/settle
  the ledger. In MOCK mode (no Monnify key) verify_webhook accepts, so the routes
  are testable offline.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings

from utility import providers as P
from wallet.models import FundingIntent, Transaction, Wallet
from wallet.tests import make_user

MONNIFY_LIVE = {"BASE_URL": "https://sandbox.monnify.com", "API_KEY": "MK_TEST_x",
                "SECRET_KEY": "sk_test_x", "CONTRACT_CODE": "123", "REDIRECT_URL": "",
                "WALLET_ACCOUNT": "3000000001", "SIMULATION": False}
WEMA_LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
             "KEYS": {"wallet": "subkey"}, "SOURCE_ACCOUNT": "0100000001",
             "SECURITY_INFO": "sec", "SIMULATION": False}


class ProviderSelectionTests(SimpleTestCase):
    """Monnify is the default rail; Wema is opt-in via the *_PROVIDER env vars."""

    def test_money_rail_defaults_to_monnify(self):
        self.assertEqual(P.payment_provider(), "monnify")
        self.assertEqual(P.payout_provider(), "monnify")

    @override_settings(PAYOUT_PROVIDER="wema")
    def test_payout_provider_explicit_wema(self):
        self.assertEqual(P.payout_provider(), "wema")

    @override_settings(PAYOUT_PROVIDER="wema", WEMA=WEMA_LIVE)
    def test_payout_live_tracks_wema_keys_when_selected(self):
        self.assertTrue(P.payout_live())

    def test_card_provider_defaults_to_issuer(self):
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(CARD_PROVIDER="kora")
    def test_card_provider_ignores_removed_kora(self):
        # "kora" is no longer a card backend — the selector falls back to issuer.
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(MONNIFY=MONNIFY_LIVE)
    def test_payout_live_tracks_monnify_keys(self):
        self.assertTrue(P.payout_live())


class FundingDispatchTests(SimpleTestCase):
    def test_funding_initialize_routes_to_monnify(self):
        with patch("utility.monnify.payment_initialize",
                   return_value={"success": True, "authorization_url": "https://m", "reference": "R"}) as m:
            P.funding_initialize("a@b.com", 1000, "ZPAY1", name="Ada")
        m.assert_called_once()

    def test_funding_verify_routes_to_monnify(self):
        with patch("utility.monnify.payment_verify", return_value={"success": True}) as m:
            P.funding_verify("ZPAY1")
        m.assert_called_once()

    def test_funding_account_reserve_routes_to_monnify(self):
        with patch("utility.monnify.create_virtual_account",
                   return_value={"success": True, "account_number": "990", "reference": "r"}) as m:
            P.funding_account_reserve("usr-1", "Ada", "a@b.com", "Ada", bvn="22212345678")
        m.assert_called_once()


class PayoutDispatchTests(SimpleTestCase):
    def test_payout_send_routes_to_monnify(self):
        with patch("utility.monnify.disburse", return_value={"success": True, "status": "pending"}) as m:
            P.payout_send(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        m.assert_called_once()

    def test_payout_resolve_routes_to_monnify(self):
        with patch("utility.monnify.resolve_account", return_value={"success": True, "name": "ADA"}) as m:
            P.payout_resolve_account("0123456789", "058")
        m.assert_called_once_with("0123456789", "058")

    @override_settings(PAYOUT_PROVIDER="wema", WEMA=WEMA_LIVE)
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

    @override_settings(PAYOUT_PROVIDER="wema", WEMA=WEMA_LIVE)
    def test_payout_resolve_routes_to_wema(self):
        with patch("utility.wema.resolve_account",
                   return_value={"success": True, "name": "ADA EZE"}) as m:
            P.payout_resolve_account("0123456789", "035")
        m.assert_called_once_with("0123456789", "035")

    @override_settings(PAYOUT_PROVIDER="wema", WEMA={**WEMA_LIVE, "SOURCE_ACCOUNT": ""})
    def test_payout_send_fails_closed_without_source_account(self):
        # Live Wema payout with no sender NUBAN and no pool must refuse (refundable)
        # rather than send an empty sourceAccountNumber. wema.transfer must NOT be called.
        with patch("utility.wema.transfer") as m:
            out = P.payout_send(1000, "ZTRF1", "note", "035", "0123456789", "ADA EZE", bank_name="Wema Bank")
        self.assertFalse(out["success"])
        m.assert_not_called()

    @override_settings(PAYOUT_PROVIDER="wema", WEMA=WEMA_LIVE)
    def test_payout_send_prefers_sender_nuban_over_pool(self):
        # Per-user-balance model: debit the SENDER's own NUBAN, not the pool.
        with patch("utility.wema.transfer", return_value={"success": True, "status": "SUCCESS"}) as m:
            P.payout_send(1000, "ZTRF1", "note", "035", "0123456789", "ADA EZE",
                          bank_name="Wema Bank", source_account="0199999999")
        self.assertEqual(m.call_args.kwargs["source_account"], "0199999999")  # sender NUBAN wins over pool


class KycDispatchTests(SimpleTestCase):
    """Production rails: BVN/NIN on Monnify (the sole rail); vNIN on Prembly."""

    def test_kyc_provider_is_monnify(self):
        self.assertEqual(P.kyc_provider(), "monnify")

    @override_settings(KYC_PROVIDER="kora")
    def test_removed_kora_choice_falls_back_to_monnify(self):
        self.assertEqual(P.kyc_provider(), "monnify")

    @override_settings(MONNIFY=MONNIFY_LIVE)
    def test_verify_bvn_routes_to_monnify(self):
        with patch("utility.monnify.verify_bvn", return_value={"success": True}) as mm:
            P.verify_bvn("22222222222", name="Ada Eze", mobile="080")
        mm.assert_called_once()
        self.assertEqual(mm.call_args.kwargs["name"], "Ada Eze")

    @override_settings(MONNIFY=MONNIFY_LIVE)
    def test_verify_nin_routes_to_monnify(self):
        with patch("utility.monnify.verify_nin", return_value={"success": True}) as mm:
            P.verify_nin("12345678901")
        mm.assert_called_once_with("12345678901")

    def test_vnin_routes_to_prembly(self):
        with patch("utility.providers.kyc_verify_vnin", return_value={"success": True}) as mk:
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


class FundingAccountGetDispatchTests(SimpleTestCase):
    @override_settings(PAYMENT_PROVIDER="wema")
    def test_funding_account_get_wema_does_not_hit_monnify(self):
        # Must NOT fall through to monnify.get_virtual_account for a Wema account.
        with patch("utility.monnify.get_virtual_account") as mm:
            out = P.funding_account_get("ZITCH-WALLET-1")
        mm.assert_not_called()
        self.assertFalse(out["success"])
        self.assertTrue(out.get("otp_required"))


class CardDispatchTests(SimpleTestCase):
    def test_card_issue_routes_to_generic_issuer(self):
        # Monnify issues no cards — card_issue delegates to the generic CARD_ISSUER.
        with patch("utility.providers.issue_card",
                   return_value={"success": True, "card_token": "card_1"}) as m:
            out = P.card_issue("ADA EZE", "42", email="ada@b.com")
        m.assert_called_once()
        self.assertTrue(out["success"])


class MonnifyFundingWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000001", "fund@zitch.app")

    def _post(self, payload):
        return self.client.post("/api/fund/monnify/webhook/", data=json.dumps(payload),
                                content_type="application/json",
                                HTTP_MONNIFY_SIGNATURE="mock")

    def test_checkout_success_credits_funding_intent(self):
        FundingIntent.objects.create(user=self.user, reference="ZPAYM1", amount=Decimal("2500"),
                                     meta={"provider": "monnify"})
        res = self._post({"eventType": "SUCCESSFUL_TRANSACTION",
                          "eventData": {"product": {"type": "WEB_SDK"},
                                        "paymentReference": "ZPAYM1",
                                        "transactionReference": "MNFY-TX-1", "amountPaid": "2500"}})
        self.assertEqual(res.status_code, 200)
        intent = FundingIntent.objects.get(reference="ZPAYM1")
        self.assertTrue(intent.credited)
        self.assertEqual(get_balance(self.user), Decimal("2500"))

    def test_reserved_account_credit_maps_by_account_number(self):
        w = Wallet.objects.get(user=self.user)
        w.account_number = "9900000001"
        w.account_reference = "ZITCH-WALLET-X"
        w.save(update_fields=["account_number", "account_reference"])
        payload = {"eventType": "SUCCESSFUL_TRANSACTION",
                   "eventData": {"product": {"type": "RESERVED_ACCOUNT"},
                                 "transactionReference": "MNFY-TX-9", "amountPaid": "1500",
                                 "destinationAccountInformation": {"accountNumber": "9900000001"}}}
        res = self._post(payload)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_balance(self.user), Decimal("1500"))
        # Idempotent: a redelivered event keyed on the same txref does not double-credit.
        self._post(payload)
        self.assertEqual(get_balance(self.user), Decimal("1500"))


class MonnifyPayoutWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, payload):
        return self.client.post("/api/transfers/webhook/", data=json.dumps(payload),
                                content_type="application/json",
                                HTTP_MONNIFY_SIGNATURE="mock")

    def test_failed_disbursement_reverses(self):
        with patch("transfers.views.reverse_transfer") as m:
            res = self._post({"eventType": "FAILED_DISBURSEMENT", "eventData": {"reference": "ZTRF9"}})
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("ZTRF9")

    def test_successful_disbursement_settles(self):
        with patch("transfers.views.settle_payout") as m:
            res = self._post({"eventType": "SUCCESSFUL_DISBURSEMENT", "eventData": {"reference": "ZTRF9"}})
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("ZTRF9")


def get_balance(user) -> Decimal:
    return Wallet.objects.get(user=user).balance
