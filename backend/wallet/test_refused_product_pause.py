"""A product the bank refuses at authentication stops taking payments for a while.

From production, 16 to 26 November 2026: Wema answered "Authentication Failed"
to every electricity payment. Each customer was debited, refused and refunded,
and each was told to try again, so the next one did exactly the same.
"""
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

from wallet.models import Transaction
from wallet.services import (LimitExceeded, credit, get_or_create_wallet,
                             product_refusal_paused, run_provider_purchase)

User = get_user_model()
REFUSED = {"success": False, "message": "Authentication Failed"}
DELIVERED = {"success": True}


class RefusedProductPauseTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create(
            username="p1", phone="08011113333", email="p1@zitch.test",
            first_name="Ada", last_name="Eze", tier=1, email_verified=True,
            phone_verified=True, bvn_verified=True, nin_verified=True)
        get_or_create_wallet(self.user)
        credit(self.user, Decimal("5000.00"), "test-topup")

    def _buy(self, service="Electricity - Ikeja", result=REFUSED):
        provider = mock.Mock(return_value=result)
        outcome = run_provider_purchase(self.user, Decimal("1000.00"), service, {}, provider)
        return outcome, provider

    def _balance(self):
        return get_or_create_wallet(self.user).balance

    def test_the_next_customer_is_turned_away_before_any_debit(self):
        (status, _txn, _result), _ = self._buy()
        self.assertEqual(status, "failed")
        self.assertTrue(product_refusal_paused("Electricity - Eko"))
        rows = Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count()

        provider = mock.Mock(return_value=REFUSED)
        with self.assertRaises(LimitExceeded) as turned_away:
            run_provider_purchase(self.user, Decimal("1000.00"), "Electricity - Eko", {}, provider)

        self.assertIn("not been charged", str(turned_away.exception))
        provider.assert_not_called()
        self.assertEqual(
            Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count(), rows)
        self.assertEqual(self._balance(), Decimal("5000.00"))

    def test_other_products_keep_selling(self):
        self._buy()
        (status, _txn, _result), provider = self._buy("Airtime - MTN", DELIVERED)
        self.assertEqual(status, "success")
        provider.assert_called_once()

    def test_a_refusal_the_customer_can_fix_pauses_nothing(self):
        self._buy(result={"success": False, "message": "Meter number not found"})
        self.assertFalse(product_refusal_paused("Electricity - Ikeja"))

    def test_the_pause_ends_and_a_delivered_payment_clears_it(self):
        self._buy()
        cache.delete("vas:refused:electricity")   # the pause running out
        (status, _txn, _result), _ = self._buy(result=DELIVERED)
        self.assertEqual(status, "success")
        self.assertFalse(product_refusal_paused("Electricity - Ikeja"))

    @override_settings(VAS_REFUSED_PAUSE_SECONDS=0)
    def test_the_pause_can_be_switched_off(self):
        self._buy()
        self.assertFalse(product_refusal_paused("Electricity - Ikeja"))
