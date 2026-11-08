"""Tests for the Fixed Save flow: rates, quote, lock (create), list, maturity.

These also pin the JSON contract the Expo app depends on (the `My Fixed Saves`
screen reads `savings/list`, and `fixedsave` reads `savings/rates`), so a future
field rename here fails loudly instead of silently breaking the app.

Locking is a customer debit and now answers to the same gate as every other one,
so the fixture here is a customer who can actually spend — verified contact
details, proved identity, an active bank account with a bank-confirmed balance.
That is the shared `make_bank_user` fixture the rest of the suite uses, not a
hand-rolled one, so a change to what spending requires reaches these tests too.
`SavingsSpendGateTests` at the bottom covers the refusals.
"""
import json
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import IdentityProof, User
from banking.models import BankAccount
from banking.test_client import CONFIG
from banking.test_fixtures import make_bank_user
from common.http import daily_kind_for
from wallet.models import Transaction
from wallet.services import InsufficientFunds, LimitExceeded, get_or_create_wallet

from .models import FixedSave
from .services import LOCK_SERVICE, lock, run_maturities


def make_saver(phone="08010000001", email="saver@zitch.test", *, tier=2,
               balance="250000", bank_balance=None):
    """A customer who passes the spending gate, with `balance` in the wallet.

    Tier 2 because the Fixed Save contract tests lock six figures, which a Tier 1
    per-transaction ceiling (50,000) refuses. `face_verified` because a spend at
    or above RISK_NEW_DEVICE_FACE_THRESHOLD from a device the account has never
    used needs the step-up, and the Django test client never sends a device
    header — so without it every lock over 50,000 would be refused for a reason
    that has nothing to do with savings.
    """
    user, token = make_bank_user(phone, email, balance=balance, tier=tier,
                                 bank_balance=bank_balance)
    user.face_verified = True
    user.save(update_fields=["face_verified"])
    return user, token


@override_settings(NINEPSB=CONFIG)
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
            {"access_token": self.token, "amount": "50000", "days": 90, "transaction_pin": "1234"},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("200000"))
        plan = FixedSave.objects.get(user=self.user)
        self.assertEqual(plan.principal, Decimal("50000"))
        self.assertEqual(plan.status, FixedSave.ACTIVE)
        self.assertEqual(body["reference"], plan.reference)
        # Returned plan dict carries every field the app renders.
        for key in ("reference", "principal", "interest", "rate", "duration_days",
                    "maturity_value", "status", "matures_at"):
            self.assertIn(key, body["plan"])

    def test_create_and_replay_return_the_same_top_level_reference(self):
        payload = {
            "access_token": self.token,
            "amount": "50000",
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
            {"access_token": self.token, "amount": "50000", "days": 90,
             "transaction_pin": "1234", "idempotency_key": None},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body["code"], "idempotency_key_required")
        self.assertFalse(FixedSave.objects.exists())

    def test_create_rejects_wrong_pin(self):
        res, body = self.post(
            "/api/savings/create/",
            {"access_token": self.token, "amount": "50000", "days": 90, "transaction_pin": "0000"},
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
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "30000", "days": 30, "transaction_pin": "1234"})
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "20000", "days": 180, "transaction_pin": "1234"})
        res, body = self.post("/api/savings/list/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(body["total_locked"]), Decimal("50000"))
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
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "100000", "days": 365, "transaction_pin": "1234"})
        plan = FixedSave.objects.get(user=self.user)
        # Fast-forward maturity into the past.
        plan.matures_at = timezone.now() - timezone.timedelta(days=1)
        plan.save(update_fields=["matures_at"])

        before = get_or_create_wallet(self.user).balance  # 150000 (250k - 100k locked)
        self.assertEqual(run_maturities(), 1)
        after = get_or_create_wallet(self.user).balance
        self.assertEqual(after - before, Decimal("122000.00"))  # principal + 22% interest

        plan.refresh_from_db()
        self.assertEqual(plan.status, FixedSave.MATURED)
        self.assertTrue(plan.paid_out)

        # Re-running never double-pays.
        self.assertEqual(run_maturities(), 0)
        self.assertEqual(get_or_create_wallet(self.user).balance, after)

    def test_list_auto_settles_matured_plan(self):
        """Opening savings pays out anything that matured — no cron required."""
        self.post("/api/savings/create/", {"access_token": self.token, "amount": "100000", "days": 365, "transaction_pin": "1234"})
        plan = FixedSave.objects.get(user=self.user)
        plan.matures_at = timezone.now() - timezone.timedelta(days=1)
        plan.save(update_fields=["matures_at"])

        before = get_or_create_wallet(self.user).balance  # 150000 (250k - 100k locked)
        _, body = self.post("/api/savings/list/", {"access_token": self.token})
        after = get_or_create_wallet(self.user).balance
        self.assertEqual(after - before, Decimal("122000.00"))  # principal + interest credited
        self.assertEqual(FixedSave.objects.get(pk=plan.pk).status, FixedSave.MATURED)
        self.assertEqual(body["plans"][0]["status"], FixedSave.MATURED)
        # Idempotent: opening again must not double-credit.
        self.post("/api/savings/list/", {"access_token": self.token})
        self.assertEqual(get_or_create_wallet(self.user).balance, after)


@override_settings(NINEPSB=CONFIG)
class SavingsSpendGateTests(TestCase):
    """Locking money answers to the same gate as every other customer debit.

    Savings used to be the one debit that did not. An account the platform
    refused to let spend — frozen, PND'd, balance unconfirmed, short of its
    identity floor, or over its tier ceiling — could still move its whole wallet
    into a Fixed Save, and the money returned at maturity as an ordinary credit.
    These tests are the refusals, and each one also checks that NOTHING was
    written: a refused lock must leave no ledger row, no plan and no debit.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_saver("08020000002", "gate@zitch.test")
        self.bank = BankAccount.objects.get(wallet__user=self.user)

    def create(self, amount="50000", days=90, token=None, key="gate-key-1"):
        return self.client.post("/api/savings/create/", data=json.dumps({
            "access_token": token or self.token, "amount": amount, "days": days,
            "transaction_pin": "1234", "idempotency_key": key,
        }), content_type="application/json")

    def assertNothingMoved(self, user, expected="250000"):
        self.assertEqual(get_or_create_wallet(user).balance, Decimal(expected))
        self.assertFalse(FixedSave.objects.filter(user=user).exists())
        self.assertFalse(Transaction.objects.filter(
            user=user, direction=Transaction.OUT).exists())

    # --- account standing ---
    def test_a_frozen_bank_account_cannot_lock_savings(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(frozen=True)
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("50000"), 90, idempotency_key="frozen-1")
        self.assertNothingMoved(self.user)

    def test_a_pnd_account_cannot_lock_savings(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(pnd=True)
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("50000"), 90, idempotency_key="pnd-1")
        self.assertNothingMoved(self.user)

    def test_an_account_awaiting_balance_review_cannot_lock_savings(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(opening_review_required=True)
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("50000"), 90, idempotency_key="review-1")
        self.assertNothingMoved(self.user)

    def test_a_stale_bank_balance_cannot_lock_savings(self):
        # The gate requires a balance confirmed in the last five minutes.
        BankAccount.objects.filter(pk=self.bank.pk).update(
            checked_at=timezone.now() - timezone.timedelta(hours=2))
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("50000"), 90, idempotency_key="stale-1")
        self.assertNothingMoved(self.user)

    def test_an_account_without_proved_identity_cannot_lock_savings(self):
        IdentityProof.objects.filter(user=self.user).delete()
        User.objects.filter(pk=self.user.pk).update(
            bvn_verified=False, nin_verified=False,
            bvn_hash="", nin_hash="", bvn_last4="", nin_last4="")
        self.user.refresh_from_db()
        with self.assertRaises(LimitExceeded):
            lock(self.user, Decimal("50000"), 90, idempotency_key="identity-1")
        self.assertNothingMoved(self.user)

    # --- amount ---
    def test_a_lock_above_the_tier_ceiling_is_refused(self):
        tier1, _ = make_saver("08020000003", "tier1@zitch.test", tier=1)
        self.assertEqual(tier1.transaction_limit, Decimal("50000"))
        with self.assertRaises(LimitExceeded):
            lock(tier1, Decimal("100000"), 365, idempotency_key="ceiling-1")
        self.assertNothingMoved(tier1)

    def test_the_bank_confirmed_balance_caps_a_lock_not_the_stored_one(self):
        """The wallet says 250,000; the bank has confirmed 10,000. 10,000 wins.

        This is the check savings was missing entirely: it compared the principal
        against `wallet.balance` alone, which can exceed what the bank actually
        holds — the exact condition reconciliation exists to catch.
        """
        thin, token = make_saver("08020000004", "thin@zitch.test", bank_balance="10000")
        with self.assertRaises(InsufficientFunds) as caught:
            lock(thin, Decimal("50000"), 90, idempotency_key="thin-1")
        self.assertEqual(str(caught.exception), "Insufficient available balance")
        self.assertNothingMoved(thin)

        response = self.create(token=token, key="thin-http-1")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["message"], "Insufficient available balance")

    # --- the HTTP surface ---
    def test_create_answers_403_limit_exceeded_and_names_the_reason(self):
        BankAccount.objects.filter(pk=self.bank.pk).update(frozen=True)
        response = self.create()
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertEqual(body["code"], "limit_exceeded")
        self.assertTrue(body["message"])
        self.assertNothingMoved(self.user)

    # --- label parity ---
    def test_the_label_the_caps_are_checked_against_is_the_label_written(self):
        """One constant, so the bucket measured can't drift from the bucket counted."""
        plan = lock(self.user, Decimal("50000"), 90, idempotency_key="label-1")
        row = Transaction.objects.get(reference=plan.reference)
        self.assertEqual(row.service, LOCK_SERVICE)

    def test_savings_is_deliberately_in_no_daily_bucket(self):
        """Locking is not a transfer out and not a bill, so it consumes neither
        daily cap. Asserted rather than assumed: if a future label change swept
        savings into a bucket, customers would silently lose transfer headroom
        to money that never left the bank."""
        self.assertEqual(daily_kind_for(LOCK_SERVICE), "")
