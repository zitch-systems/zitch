"""Tests for the Fixed Save flow: rates, quote, lock (create), list, maturity.

These also pin the JSON contract the Expo app depends on (the `My Fixed Saves`
screen reads `savings/list`, and `fixedsave` reads `savings/rates`), so a future
field rename here fails loudly instead of silently breaking the app.

Locking is a customer debit and now answers to the same gate as every other one,
so the fixture here is a customer who can actually spend — verified contact
details, proved identity, an active bank account with a bank-confirmed balance.
There is no bank any more, so the fixture is a plain local-ledger customer and
every account is Tier 0. `SavingsBanklessSpendTests` at the bottom covers what
still refuses a lock once there is no bank to consult.
"""
import json
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from common.http import daily_kind_for
from wallet.models import Transaction
from wallet.services import InsufficientFunds, LimitExceeded, get_or_create_wallet
from wallet.tests import make_user

from .models import FixedSave
from .services import LOCK_SERVICE, lock, run_maturities


def make_saver(phone="08010000001", email="saver@zitch.test", balance="250000"):
    """A customer of a platform with no bank, which is now the only kind.

    No BankAccount is created, because none can exist: the 9PSB integration is
    gone and `account_for` returns None for everyone. Available funds therefore
    come from the local ledger, which is what makes a lock possible at all.

    Every such account is Tier 0, capped at 20,000 per transaction. That is not
    a choice made here -- `User.recompute_tier` grants a tier only when a bank
    account reports one, so with no bank nobody earns one. The amounts below sit
    under that cap for exactly that reason.
    """
    return make_user(phone, email, balance=balance)


class SavingsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_saver()
        self._key_seq = 0

    def post(self, path, payload):
        payload = dict(payload)
        if path == "/api/savings/create/" and "idempotency_key" not in payload:
            self._key_seq += 1
            payload["idempotency_key"] = f"save-test-{self._key_seq}"
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    # --- rates / quote (no auth) ---
    def test_rates_lists_every_tier(self):
        res, body = self.post("/api/savings/rates/", {})
        self.assertEqual(res.status_code, 200)
        days = {r["days"] for r in body["rates"]}
        self.assertEqual(days, set(FixedSave.RATES))
        self.assertEqual(body["min"], str(FixedSave.MIN_PRINCIPAL))

    def test_quote_matches_simple_interest_formula(self):
        # A quote never debits, so no spending ceiling applies to it.
        res, body = self.post("/api/savings/quote/", {"access_token": self.token, "amount": "100000", "days": 365})
        self.assertEqual(res.status_code, 200)
        # 100000 * 0.22 * (365/365) = 22000.00
        self.assertEqual(body["interest"], "22000.00")
        self.assertEqual(body["maturity_value"], "122000.00")

    def test_quote_rejects_unknown_period(self):
        res, body = self.post("/api/savings/quote/", {"access_token": self.token, "amount": "1000", "days": 45})
        self.assertEqual(res.status_code, 400)

    # --- create (lock) ---
    def test_create_locks_funds_and_debits_wallet(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "10000", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("240000"))
        plan = FixedSave.objects.get(user=self.user)
        self.assertEqual(plan.principal, Decimal("10000"))
        self.assertEqual(plan.status, FixedSave.ACTIVE)
        self.assertEqual(body["reference"], plan.reference)
        # Returned plan dict carries every field the app renders.
        for key in ("reference", "principal", "interest", "rate", "duration_days",
                    "maturity_value", "status", "matures_at"):
            self.assertIn(key, body["plan"])

    def test_create_and_replay_return_the_same_top_level_reference(self):
        payload = {
            "access_token": self.token,
            "amount": "10000",
            "days": 90,
            "transaction_pin": "1234",
            "idempotency_key": "save-reference-parity",
        }

        first, first_body = self.post("/api/savings/create/", payload)
        replay, replay_body = self.post("/api/savings/create/", payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay_body["duplicate"])
        self.assertEqual(first_body["reference"], replay_body["reference"])
        self.assertEqual(first_body["reference"], first_body["plan"]["reference"])

    def test_create_requires_a_client_idempotency_key(self):
        response, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "10000", "days": 90,
             "transaction_pin": "1234", "idempotency_key": None},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "idempotency_key_required")
        self.assertFalse(FixedSave.objects.exists())

    def test_create_rejects_wrong_pin(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "10000", "days": 90, "transaction_pin": "0000"},
        )
        self.assertEqual(res.status_code, 403)
        self.assertEqual(FixedSave.objects.count(), 0)

    def test_create_rejects_below_minimum(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "500", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 400)

    def test_create_rejects_when_insufficient(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "500000", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 402)

    # --- list ---
    def test_list_returns_total_locked_and_plan_contract(self):
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "10000", "days": 30, "transaction_pin": "1234"})
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "5000", "days": 180, "transaction_pin": "1234"})
        res, body = self.post("/api/savings/list/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(body["total_locked"]), Decimal("15000"))
        self.assertEqual(len(body["plans"]), 2)
        self.assertEqual(set(body["plans"][0]), {
            "reference", "principal", "interest", "rate", "duration_days",
            "maturity_value", "status", "matures_at",
        })

    def test_list_requires_auth(self):
        res, _ = self.post("/api/savings/list/", {"access_token": "bogus"})
        self.assertEqual(res.status_code, 401)

    # --- maturity payout ---
    def test_maturity_pays_principal_plus_interest_once(self):
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "10000", "days": 365, "transaction_pin": "1234"})
        plan = FixedSave.objects.get(user=self.user)
        # Fast-forward maturity into the past.
        plan.matures_at = timezone.now() - timezone.timedelta(days=1)
        plan.save(update_fields=["matures_at"])

        before = get_or_create_wallet(self.user).balance  # 240000 (250k - 10k locked)
        self.assertEqual(run_maturities(), 1)
        after = get_or_create_wallet(self.user).balance
        self.assertEqual(after - before, Decimal("12200.00"))  # principal + 22% interest

        plan.refresh_from_db()
        self.assertEqual(plan.status, FixedSave.MATURED)
        self.assertTrue(plan.paid_out)

        # Re-running never double-pays.
        self.assertEqual(run_maturities(), 0)
        self.assertEqual(get_or_create_wallet(self.user).balance, after)

    def test_list_auto_settles_matured_plan(self):
        """Opening savings pays out anything that matured — no cron required."""
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "10000", "days": 365, "transaction_pin": "1234"})
        plan = FixedSave.objects.get(user=self.user)
        plan.matures_at = timezone.now() - timezone.timedelta(days=1)
        plan.save(update_fields=["matures_at"])

        before = get_or_create_wallet(self.user).balance  # 240000 (250k - 10k locked)
        _, body = self.post("/api/savings/list/", {"access_token": self.token})
        after = get_or_create_wallet(self.user).balance
        self.assertEqual(after - before, Decimal("12200.00"))  # principal + interest credited
        self.assertEqual(FixedSave.objects.get(pk=plan.pk).status, FixedSave.MATURED)
        self.assertEqual(body["plans"][0]["status"], FixedSave.MATURED)
        # Idempotent: opening again must not double-credit.
        self.post("/api/savings/list/", {"access_token": self.token})
        self.assertEqual(get_or_create_wallet(self.user).balance, after)


class SavingsBanklessSpendTests(TestCase):
    """What still gates a lock once there is no bank to consult.

    This replaces a suite that covered the bank's own judgement -- frozen, PND,
    awaiting balance review, a stale balance reading. None of those can happen
    now: `configured()` is false, so `lock` skips the standing check entirely.
    Keeping those tests would have been worse than deleting them: they went on
    passing after the bank was removed, because a 50,000 lock tripped the Tier 0
    ceiling instead. Green for a reason the test never claimed is a false green.

    What remains is real, and is asserted here: the tier ceiling, the local
    available balance, and the rule that a refused lock writes nothing.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_saver("08020000002", "gate@zitch.test")

    def create(self, amount="10000", days=90, token=None, key="gate-key-1"):
        return self.client.post("/api/savings/create/", data=json.dumps({
            "access_token": token or self.token, "amount": amount, "days": days,
            "transaction_pin": "1234", "idempotency_key": key,
        }), content_type="application/json")

    def assertNothingMoved(self, user, expected="250000"):
        self.assertEqual(get_or_create_wallet(user).balance, Decimal(expected))
        self.assertFalse(FixedSave.objects.filter(user=user).exists())
        self.assertFalse(Transaction.objects.filter(
            user=user, direction=Transaction.OUT).exists())

    def test_every_account_is_tier_0_because_tiers_are_bank_granted(self):
        """Not an incidental fixture detail — the ceiling every test below sits under.

        `User.recompute_tier` grants a tier only when a bank account reports one.
        With no bank nobody earns one, so the per-transaction ceiling is 20,000
        for everyone. Asserted so that a future change which silently restores a
        higher tier without a verifier behind it fails here.
        """
        # The stored tier is whatever it was last set to; what matters is what
        # recompute_tier -- the only thing that grants one -- now returns.
        self.user.recompute_tier()
        self.assertEqual(self.user.tier, 0)
        self.assertEqual(self.user.transaction_limit, Decimal("20000"))

    def test_a_lock_above_the_tier_ceiling_is_refused(self):
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("25000"), 90, idempotency_key="ceiling-1")
        self.assertNothingMoved(self.user)

    def test_a_lock_within_the_ceiling_succeeds(self):
        """The point of the whole ledger change: a bankless wallet can spend."""
        plan = lock(self.user, Decimal("10000"), 90, idempotency_key="ok-1")
        self.assertEqual(plan.principal, Decimal("10000"))
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("240000"))

    def test_unsettled_historical_funds_are_still_not_spendable(self):
        """The one restriction that outlived the bank.

        Wema-era funds whose settlement was never evidenced do not become
        spendable because the bank that held them is gone. `wallet_balance_payload`
        claims this holds with no provider configured; this is that claim tested.
        """
        from banking.models import PreviousBankArchive
        wallet = get_or_create_wallet(self.user)
        PreviousBankArchive.objects.create(
            wallet=wallet, balance=Decimal("245000"), account_number="old")
        with self.assertRaises(InsufficientFunds) as caught:
            lock(self.user, Decimal("10000"), 90, idempotency_key="archive-1")
        self.assertEqual(str(caught.exception), "Insufficient available balance")
        self.assertNothingMoved(self.user)

    def test_create_answers_403_limit_exceeded_and_names_the_reason(self):
        response = self.create(amount="25000")
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["code"], "limit_exceeded")
        self.assertTrue(body["message"])
        self.assertNothingMoved(self.user)

    def test_the_label_the_caps_are_checked_against_is_the_label_written(self):
        """One constant, so the bucket measured can't drift from the bucket counted."""
        plan = lock(self.user, Decimal("10000"), 90, idempotency_key="label-1")
        row = Transaction.objects.get(reference=plan.reference)
        self.assertEqual(row.service, LOCK_SERVICE)

    def test_savings_is_deliberately_in_no_daily_bucket(self):
        """Locking is not a transfer out and not a bill, so it consumes neither
        daily cap. Asserted rather than assumed: if a future label change swept
        savings into a bucket, customers would silently lose transfer headroom."""
        self.assertEqual(daily_kind_for(LOCK_SERVICE), "")
