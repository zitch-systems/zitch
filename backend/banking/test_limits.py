"""Only configured bank outgoing limits govern the new bank account."""
import json
from decimal import Decimal
from django.test import TestCase, override_settings
from wallet.models import Transaction
from .models import BankAccount
from .payments import bank_limit_error
from .test_client import CONFIG
from .test_money import MoneyTests


@override_settings(NINEPSB=CONFIG)
class OutgoingLimitTests(TestCase):
    def setUp(self):
        MoneyTests.setUp(self)

    def limits(self, *, single=None, daily=None):
        return override_settings(NINEPSB={**CONFIG,
            "SINGLE_OUTBOUND_LIMITS": json.dumps({"1": single} if single is not None else {}),
            "DAILY_OUTBOUND_LIMITS": json.dumps({"1": daily} if daily is not None else {})})

    def spend(self, amount, *, status=Transaction.SUCCESS, provider="9psb", currency="NGN", direction=Transaction.OUT):
        return Transaction.objects.create(user=self.user, reference=f"limit-{Transaction.objects.count()}",
            amount=amount, service="Payment", direction=direction, transaction_status=status,
            currency=currency, meta={"provider": provider})

    def test_absent_limits_do_not_invent_a_cap_from_deposit_or_balance_limits(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(maximum_deposit=1, maximum_balance=2)
        with self.limits():
            self.assertIsNone(bank_limit_error(self.user, Decimal("10000")))

    def test_current_account_is_required(self):
        from wallet.tests import make_user
        self.user, _ = make_user("08099991234", "unbanked-limit@example.test")
        self.assertIn("setup", bank_limit_error(self.user, Decimal("1")))

    def test_single_limit_allows_exactly_the_cap_and_rejects_one_kobo_more(self):
        with self.limits(single="100"):
            self.assertIsNone(bank_limit_error(self.user, Decimal("100")))
            self.assertIn("single", bank_limit_error(self.user, Decimal("100.01")))

    def test_daily_limit_counts_pending_and_successful_current_bank_debits_once(self):
        self.spend("60")
        self.spend("30", status=Transaction.PENDING)
        with self.limits(daily="100"):
            self.assertIsNone(bank_limit_error(self.user, Decimal("10")))
            self.assertIn("daily", bank_limit_error(self.user, Decimal("10.01")))

    def test_failed_historical_foreign_currency_and_credits_do_not_consume_new_bank_limit(self):
        self.spend("100", status=Transaction.FAILED)
        self.spend("100", provider="wema")
        self.spend("100", currency="USD")
        self.spend("100", direction=Transaction.IN)
        with self.limits(daily="100"):
            self.assertIsNone(bank_limit_error(self.user, Decimal("100")))

    def test_malformed_negative_or_nonfinite_limits_fail_closed(self):
        for value in ("NaN", "Infinity", "-1", "bad"):
            with self.subTest(value=value), self.limits(daily=value):
                self.assertIn("configuration", bank_limit_error(self.user, Decimal("1")))

    def test_bank_tier_selects_only_its_configured_outgoing_limit(self):
        with self.limits(single="100"):
            BankAccount.objects.filter(pk=self.bank.pk).update(tier=2)
            self.assertIsNone(bank_limit_error(self.user, Decimal("101")))
