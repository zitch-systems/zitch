"""Tests for the payment/payout/card provider-selection dispatch layer and the
Kora webhook endpoints (utility.providers dispatch + wallet/transfers views).

Two layers:
- Pure routing (SimpleTestCase): the *_provider() selectors and the funding_* /
  payout_* / card_* wrappers delegate to the Kora client.
- Webhook crediting (TestCase): the Kora pay-in/payout webhooks credit/settle
  the ledger. In MOCK mode (no KORA key) verify_webhook accepts, so the routes
  are testable offline.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, SimpleTestCase, TestCase, override_settings

from utility import providers as P
from wallet.models import FundingIntent, Transaction, Wallet
from wallet.tests import make_user

KORA_LIVE = {"BASE_URL": "https://api.korapay.com/merchant", "SECRET_KEY": "sk_test_x", "PUBLIC_KEY": ""}


class ProviderSelectionTests(SimpleTestCase):
    """Kora is the sole money-movement rail; cards keep the issuer/kora choice."""

    def test_money_rail_is_kora(self):
        self.assertEqual(P.payment_provider(), "kora")
        self.assertEqual(P.payout_provider(), "kora")

    def test_card_provider_defaults_to_issuer(self):
        self.assertEqual(P.card_provider(), "issuer")

    @override_settings(CARD_PROVIDER="kora")
    def test_card_provider_explicit_kora(self):
        self.assertEqual(P.card_provider(), "kora")

    @override_settings(KORA=KORA_LIVE)
    def test_payout_live_tracks_kora_keys(self):
        self.assertTrue(P.payout_live())


class FundingDispatchTests(SimpleTestCase):
    def test_funding_initialize_routes_to_kora(self):
        with patch("utility.kora.payment_initialize",
                   return_value={"success": True, "authorization_url": "https://k", "reference": "R"}) as m:
            P.funding_initialize("a@b.com", 1000, "ZPAY1", name="Ada")
        m.assert_called_once()

    def test_funding_verify_routes_to_kora(self):
        with patch("utility.kora.payment_verify", return_value={"success": True}) as m:
            P.funding_verify("ZPAY1")
        m.assert_called_once()

    def test_funding_account_reserve_routes_to_kora(self):
        with patch("utility.kora.create_virtual_account",
                   return_value={"success": True, "account_number": "880", "reference": "r"}) as m:
            P.funding_account_reserve("usr-1", "Ada", "a@b.com", "Ada", bvn="22212345678")
        m.assert_called_once()


class PayoutDispatchTests(SimpleTestCase):
    def test_payout_send_routes_to_kora(self):
        with patch("utility.kora.disburse", return_value={"success": True, "status": "processing"}) as m:
            P.payout_send(1000, "ZTRF1", "note", "058", "0123456789", "ADA EZE")
        m.assert_called_once()

    def test_payout_resolve_routes_to_kora(self):
        with patch("utility.kora.resolve_account", return_value={"success": True, "name": "ADA"}) as m:
            P.payout_resolve_account("0123456789", "058")
        m.assert_called_once()


class CardDispatchTests(SimpleTestCase):
    @override_settings(CARD_PROVIDER="kora", KORA=KORA_LIVE)
    def test_card_issue_two_step_on_kora(self):
        with patch("utility.kora.create_cardholder",
                   return_value={"success": True, "reference": "chr_1"}) as mc, \
             patch("utility.kora.create_card",
                   return_value={"success": True, "card_token": "card_1"}) as mk:
            out = P.card_issue("ADA EZE", "42", email="ada@b.com")
        mc.assert_called_once()
        mk.assert_called_once_with("chr_1")
        self.assertTrue(out["success"])

    @override_settings(CARD_PROVIDER="kora", KORA=KORA_LIVE)
    def test_card_reveal_not_supported_on_kora(self):
        out = P.card_reveal("card_1")
        self.assertFalse(out["success"])


class KoraFundingWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000001", "fund@zitch.app")

    def _post(self, payload):
        return self.client.post("/api/fund/webhook/", data=json.dumps(payload),
                                content_type="application/json",
                                HTTP_X_KORAPAY_SIGNATURE="mock")

    def test_charge_success_credits_funding_intent(self):
        FundingIntent.objects.create(user=self.user, reference="ZPAYK1", amount=Decimal("2500"),
                                     meta={"provider": "kora"})
        res = self._post({"event": "charge.success",
                          "data": {"reference": "ZPAYK1", "amount": "2500"}})
        self.assertEqual(res.status_code, 200)
        intent = FundingIntent.objects.get(reference="ZPAYK1")
        self.assertTrue(intent.credited)
        self.assertEqual(get_balance(self.user), Decimal("2500"))

    def test_virtual_account_credit_maps_by_account_number(self):
        w = Wallet.objects.get(user=self.user)
        w.account_number = "8800000001"
        w.account_reference = "ZITCH-WALLET-X"
        w.save(update_fields=["account_number", "account_reference"])
        res = self._post({"event": "charge.success",
                          "data": {"reference": "KORA-TX-9", "amount": "1500",
                                   "virtual_bank_account_details": {"account_number": "8800000001"}}})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(get_balance(self.user), Decimal("1500"))
        # Idempotent: a redelivered event keyed on the same Kora ref does not double-credit.
        self._post({"event": "charge.success",
                    "data": {"reference": "KORA-TX-9", "amount": "1500",
                             "virtual_bank_account_details": {"account_number": "8800000001"}}})
        self.assertEqual(get_balance(self.user), Decimal("1500"))


class KoraPayoutWebhookTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _post(self, payload):
        return self.client.post("/api/transfers/webhook/", data=json.dumps(payload),
                                content_type="application/json",
                                HTTP_X_KORAPAY_SIGNATURE="mock")

    def test_failed_transfer_reverses(self):
        with patch("transfers.views.reverse_transfer") as m:
            res = self._post({"event": "transfer.failed", "data": {"reference": "ZTRF9"}})
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("ZTRF9")

    def test_success_transfer_settles(self):
        with patch("transfers.views.settle_payout") as m:
            res = self._post({"event": "transfer.success", "data": {"reference": "ZTRF9"}})
        self.assertEqual(res.status_code, 200)
        m.assert_called_once_with("ZTRF9")


def get_balance(user) -> Decimal:
    return Wallet.objects.get(user=user).balance
