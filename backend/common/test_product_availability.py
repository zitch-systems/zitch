"""Live money cannot be represented by an unsupported local ledger product."""
import io
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from cards.models import VirtualCard
from cards.services import claim_card_funding
from common.http import spend_key
from common.products import MESSAGES, ProductUnavailable, product_available
from loans.services import disburse, repay
from savings.models import FixedSave
from savings.services import lock, pay_out
from utility.providers import card_capabilities, fund_card
from wallet.forex import FxError, create_fx_quote, execute_fx
from wallet.models import CurrencyWallet, FxQuote, Transaction, Wallet
from wallet.tests import make_user


PRODUCTION = {"DEBUG": False, "TESTING": False, "WEMA": {"SIMULATION": False}}


class ProductAvailabilityTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08010000671", "scope@zitch.test", balance="200000")

    def post(self, path, **data):
        return self.client.post(path, json.dumps({"access_token": self.token, **data}),
                                content_type="application/json")

    @override_settings(**PRODUCTION)
    def test_all_unsupported_products_and_unknown_names_fail_closed(self):
        for product in [*MESSAGES, "typo"]:
            with self.subTest(product=product):
                self.assertFalse(product_available(product))

    @override_settings(DEBUG=True, TESTING=False, WEMA={"SIMULATION": False})
    def test_debug_with_actual_bank_rail_cannot_enable_local_money(self):
        with patch("common.products.payout_live", return_value=True):
            for product in MESSAGES:
                self.assertFalse(product_available(product))

    @override_settings(DEBUG=True, TESTING=False, WEMA={"SIMULATION": False})
    def test_debug_with_actual_fx_or_card_key_still_refuses_execution(self):
        with patch("common.products.payout_live", return_value=False), \
             patch("common.products.fincra_live", return_value=True), \
             patch("utility.providers._card_issuer_live", return_value=True):
            self.assertFalse(product_available("fx"))
            self.assertFalse(product_available("card_funding"))

    @override_settings(**PRODUCTION)
    def test_unavailable_new_requests_stop_before_pin_provider_or_money(self):
        cases = [
            ("/api/savings/create/", {"amount": "10000", "days": 30}, "savings.views.verify_transaction_pin"),
            ("/api/loans/request/", {"amount": "10000", "tenure_days": 30}, "loans.views.verify_transaction_pin"),
            ("/api/convert/airtime/", {"amount": "1000", "phone": "08010000671", "network": "1"}, "convert.views.verify_transaction_pin"),
        ]
        before = Transaction.objects.count()
        for i, (path, data, pin_path) in enumerate(cases):
            with self.subTest(path=path), patch(pin_path) as pin:
                response = self.post(path, idempotency_key=f"unavailable-{i}", **data)
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "product_unavailable")
                self.assertFalse(response.json()["product_available"])
                pin.assert_not_called()
        self.assertEqual(Transaction.objects.count(), before)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("200000"))

    @override_settings(**PRODUCTION)
    def test_rates_and_quotes_do_not_advertise_unsupported_yields(self):
        for path in ("/api/savings/rates/", "/api/convert/rates/"):
            response = self.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["rates"], [])
            self.assertFalse(response.json()["product_available"])
        for path in ("/api/savings/quote/", "/api/loans/quote/"):
            response = self.post(path, amount="10000", days=30, tenure_days=30)
            self.assertEqual(response.status_code, 503)

    def test_existing_mature_plan_is_visible_without_fabricated_bank_payout(self):
        plan = lock(self.user, Decimal("10000"), 30)
        FixedSave.objects.filter(pk=plan.pk).update(matures_at=timezone.now() - timezone.timedelta(days=1))
        before = Transaction.objects.count()
        with override_settings(**PRODUCTION), patch("savings.views.settle_user_maturities") as settle:
            response = self.post("/api/savings/list/")
        settle.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["product_available"])
        self.assertEqual(response.json()["plans"][0]["reference"], plan.reference)
        self.assertEqual(Transaction.objects.count(), before)
        plan.refresh_from_db()
        self.assertFalse(plan.paid_out)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("190000"))

    def test_existing_loan_remains_visible_and_cannot_claim_bank_repayment(self):
        loan = disburse(self.user, Decimal("10000"), 30)
        before = Transaction.objects.count()
        with override_settings(**PRODUCTION), patch("loans.views.verify_transaction_pin") as pin:
            status = self.post("/api/loans/status/").json()
            response = self.post("/api/loans/repay/", amount="1000", idempotency_key="repay-unavailable")
        self.assertEqual(status["active_loan"]["reference"], loan.reference)
        self.assertFalse(status["repayment_available"])
        self.assertEqual(status["available"], "0.00")
        self.assertEqual(response.status_code, 503)
        pin.assert_not_called()
        self.assertEqual(Transaction.objects.count(), before)
        loan.refresh_from_db()
        self.assertEqual(loan.amount_repaid, Decimal("0"))

    def test_previous_success_replays_after_product_is_disabled(self):
        key = spend_key("saved-plan", self.user, "save", Decimal("10000.00"), 30)
        plan = lock(self.user, Decimal("10000"), 30, idempotency_key=key)
        with override_settings(**PRODUCTION):
            response = self.post("/api/savings/create/", amount="10000", days=30, idempotency_key="saved-plan")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["duplicate"])
        self.assertEqual(response.json()["reference"], plan.reference)
        self.assertEqual(FixedSave.objects.count(), 1)

    def test_direct_services_and_cron_cannot_bypass_availability(self):
        plan = lock(self.user, Decimal("10000"), 30)
        loan = disburse(self.user, Decimal("10000"), 30)
        card = VirtualCard.objects.create(user=self.user, card_token="issuer-test", holder="ADA EZE", last4="1234", expiry="12/29")
        before = Transaction.objects.count()
        with override_settings(**PRODUCTION):
            calls = [lambda: lock(self.user, Decimal("1000"), 30), lambda: pay_out(plan),
                     lambda: disburse(self.user, Decimal("10000"), 30),
                     lambda: repay(self.user, loan, Decimal("1000")),
                     lambda: claim_card_funding(self.user, card, Decimal("1000"), "card-key")]
            for call in calls:
                with self.assertRaises(ProductUnavailable):
                    call()
            with self.assertRaises(CommandError):
                call_command("run_maturities", stdout=io.StringIO())
        self.assertEqual(Transaction.objects.count(), before)

    @override_settings(**PRODUCTION)
    def test_card_capabilities_and_direct_provider_refuse_unsupported_topups(self):
        self.assertFalse(card_capabilities("issuer")["can_fund"])
        with patch("utility.providers.requests.post") as request:
            result = fund_card("issuer-test", Decimal("1000"))
        self.assertFalse(result["success"])
        request.assert_not_called()

    def test_fx_execution_refuses_preexisting_quote_without_provider_or_balance_change(self):
        quote = FxQuote.objects.create(user=self.user, quote_ref="old-quote", from_currency="NGN", to_currency="USD",
                                       sell_amount="10000", receive_amount="6.50", rate="0.00065",
                                       expires_at=timezone.now() + timezone.timedelta(minutes=1))
        with override_settings(**PRODUCTION), patch("wallet.forex.fx_execute") as provider:
            with self.assertRaises(FxError):
                execute_fx(self.user, quote.quote_ref)
            with self.assertRaises(FxError):
                create_fx_quote(self.user, "NGN", "USD", "10000")
        provider.assert_not_called()
        quote.refresh_from_db()
        self.assertFalse(quote.used)
        self.assertFalse(CurrencyWallet.objects.filter(user=self.user).exists())
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("200000"))

    @override_settings(DEBUG=False, TESTING=False, WEMA={"SIMULATION": True})
    def test_deliberate_simulation_remains_available(self):
        for product in MESSAGES:
            self.assertTrue(product_available(product))
