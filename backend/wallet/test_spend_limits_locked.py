"""The spend caps must hold under a race, not merely under a polite caller.

Every daily cap and the velocity brake used to be checked only in the view, before
the wallet row was locked. Two concurrent spends therefore both read the same "spent
today", both passed, and both debited — so the caps were advisory. They are now
re-checked inside `debit()` while it holds the same `select_for_update` lock the
balance check relies on.

These tests exercise the guarantee directly: they call `debit()` with NO view in
front of it, which is exactly the position the second request in a race ends up in.
"""
from decimal import Decimal

from django.test import TestCase, override_settings

from common.http import daily_kind_for, spend_limit_error
from wallet.models import Transaction, Wallet
from wallet.services import LimitExceeded, debit, get_or_create_wallet
from wallet.tests import make_user


class DailyKindForTests(TestCase):
    def test_labels_map_to_the_bucket_they_are_counted_in(self):
        self.assertEqual(daily_kind_for("Transfer to Jane Doe"), "transfer")
        self.assertEqual(daily_kind_for("Convert NGN→USD"), "transfer")
        self.assertEqual(daily_kind_for("Airtime · MTN"), "bill")
        self.assertEqual(daily_kind_for("Card funding"), "bill")
        self.assertEqual(daily_kind_for("Exam PIN · WAEC x1"), "bill")
        self.assertEqual(daily_kind_for("Betting funding · Bet9ja"), "bill")

    def test_an_unrecognised_label_is_not_invented_into_a_bucket(self):
        # Must stay '' — guessing would apply a cap the flow never had.
        self.assertEqual(daily_kind_for("Settlement adjustment"), "")
        self.assertEqual(daily_kind_for(""), "")


@override_settings(VELOCITY_MAX_OUT_10MIN=0)     # velocity off; caps under test here
class DebitEnforcesCapsUnderTheLockTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08044440001", "lock@zitch.app", tier=2)
        Wallet.objects.filter(user=self.user).update(balance=Decimal("5000000"))

    def _spend(self, amount, label):
        return Transaction.objects.create(
            user=self.user, service=label, amount=Decimal(amount),
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference=f"R-{label[:6]}-{Transaction.objects.count()}-{amount}")

    def test_debit_refuses_a_transfer_past_the_daily_cap_with_no_view_in_front(self):
        # The position the LOSER of a race is in: it already passed the view's check.
        cap = self.user.daily_transfer_limit
        self._spend(cap, "Transfer to Someone")
        with self.assertRaises(LimitExceeded):
            debit(self.user, Decimal("1000"), "Transfer to Jane")

    def test_debit_refuses_a_bill_past_the_daily_cap(self):
        cap = self.user.daily_bill_limit
        self._spend(cap, "Airtime · MTN")
        with self.assertRaises(LimitExceeded):
            debit(self.user, Decimal("100"), "Data · MTN")

    def test_the_two_buckets_stay_independent(self):
        # Exhausting bills must not block a transfer, or the fix would be a new bug.
        self._spend(self.user.daily_bill_limit, "Airtime · MTN")
        txn = debit(self.user, Decimal("1000"), "Transfer to Jane")
        self.assertEqual(txn.transaction_status, Transaction.PENDING)

    def test_an_unbucketed_label_is_not_newly_capped(self):
        self._spend(self.user.daily_transfer_limit, "Transfer to Someone")
        debit(self.user, Decimal("1000"), "Settlement adjustment")   # must not raise

    def test_the_message_is_the_one_the_user_would_have_seen(self):
        # Carried on the exception so the raced path and the polite path can't drift.
        self._spend(self.user.daily_transfer_limit, "Transfer to Someone")
        with self.assertRaises(LimitExceeded) as ctx:
            debit(self.user, Decimal("1000"), "Transfer to Jane")
        self.assertEqual(str(ctx.exception),
                         spend_limit_error(self.user, Decimal("1000"), "Transfer to Jane"))

    def test_nothing_is_debited_when_the_cap_refuses(self):
        self._spend(self.user.daily_transfer_limit, "Transfer to Someone")
        before = Wallet.objects.get(user=self.user).balance
        with self.assertRaises(LimitExceeded):
            debit(self.user, Decimal("1000"), "Transfer to Jane")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)

    def test_enforce_limits_false_still_moves_money(self):
        # Reversals, settlements and operator actions are not customer spend and must
        # never be blocked by a cap the customer already hit.
        self._spend(self.user.daily_transfer_limit, "Transfer to Someone")
        txn = debit(self.user, Decimal("1000"), "Transfer to Jane", enforce_limits=False)
        self.assertEqual(txn.amount, Decimal("1000"))


class DebitEnforcesVelocityUnderTheLockTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08044440002", "vel@zitch.app", tier=2)
        Wallet.objects.filter(user=self.user).update(balance=Decimal("5000000"))

    @override_settings(VELOCITY_MAX_OUT_10MIN=3)
    def test_the_fraud_brake_holds_at_the_debit_not_only_at_the_view(self):
        # A stolen session racing many spends at once is exactly the case the brake
        # exists for, and exactly the case a pre-check outside the lock misses.
        for i in range(3):
            Transaction.objects.create(
                user=self.user, service="Transfer to X", amount=Decimal("100"),
                direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                reference=f"V-{i}")
        with self.assertRaises(LimitExceeded):
            debit(self.user, Decimal("100"), "Transfer to Jane")


class BankTierCapHoldsUnderTheLockTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08044440003", "bank@zitch.app", tier=2)
        Wallet.objects.filter(user=self.user).update(
            balance=Decimal("5000000"), bank_tier=1)          # bank cap 30,000/day

    @override_settings(VELOCITY_MAX_OUT_10MIN=0)
    def test_the_partner_banks_daily_cap_is_enforced_at_the_debit(self):
        Transaction.objects.create(
            user=self.user, service="Transfer to X", amount=Decimal("29000"),
            direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
            reference="BT-1", meta={"bank": "Wema Bank"})
        with self.assertRaises(LimitExceeded):
            debit(self.user, Decimal("2000"), "Transfer to Jane", meta={"bank": "Wema Bank"})
