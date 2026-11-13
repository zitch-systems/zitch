"""Tests for the Fixed Save flow: rates, quote, lock (create), list, maturity.

These also pin the JSON contract the Expo app depends on (the `My Fixed Saves`
screen reads `savings/list`, and `fixedsave` reads `savings/rates`), so a future
field rename here fails loudly instead of silently breaking the app.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken, KnownDevice
from wallet.services import credit, get_or_create_wallet

from .models import FixedSave
from .services import run_maturities

User = get_user_model()


class SavingsTests(TestCase):
    DEVICE = "dev-savings-known"

    def setUp(self):
        self.client = Client()
        # Verified, because locking funds now answers to the same gate as any
        # other debit and its identity floor refuses an unproved account. This
        # fixture predates the gate and was wholly unverified, so every lock
        # here returned 403 the moment the gate was ported in.
        # Tier 2 with face verified, because these tests lock 50,000 and
        # 100,000 and are about maturity arithmetic and the JSON contract, not
        # about caps. Tier 1 ceilings at 50,000 and 100,000 is the
        # large-transaction face threshold, so a tier-1 fixture would refuse the
        # very amounts the assertions are written around. The caps themselves
        # are exercised in SavingsSpendGateTests below.
        self.user = User.objects.create(
            username="saver", phone="08010000001", email="saver@zitch.test",
            first_name="Sade", last_name="Okoro", tier=2,
            email_verified=True, phone_verified=True, bvn_verified=True,
            nin_verified=True, face_verified=True,
        )
        self.user.set_transaction_pin("1234")
        self.user.save()
        self.token = AccessToken.issue(self.user).key
        # A device this account has used before. Locking answers to the same
        # risk brake as any other debit now, and the brake treats a request with
        # no device header as an unrecognised device -- so a 50,000 lock from the
        # bare test client was refused with "verify your face before sending this
        # much from an unrecognised device". That refusal is correct: savings was
        # bypassing the new-device step-up exactly as it was bypassing the caps.
        # The fixture names a known device instead of lowering the amount, so the
        # tests keep exercising the sums they were written for.
        KnownDevice.objects.create(user=self.user, device_id=self.DEVICE,
                                   model="Pixel 7")
        # Fund the wallet so locks succeed.
        get_or_create_wallet(self.user)
        credit(self.user, Decimal("250000"), "Test top-up")
        self._key_seq = 0

    def post(self, path, payload):
        payload = dict(payload)
        if path == "/api/savings/create/" and "idempotency_key" not in payload:
            self._key_seq += 1
            payload["idempotency_key"] = f"save-test-{self._key_seq}"
        res = self.client.post(path, data=json.dumps(payload),
                               content_type="application/json",
                               HTTP_X_ZITCH_DEVICE=self.DEVICE)
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


class SavingsSpendGateTests(TestCase):
    """Locking funds is a customer-initiated debit and answers to the same gate.

    Before the gate was added, savings was a hole in it: an account under
    balance review, deactivated, or carrying a retired destination number could
    still move its whole wallet into a 365-day plan, and the money came back out
    at maturity as a clean credit. Every other debit path refused that account.

    Written against the states wallet.services.assert_customer_spending_available
    actually checks on the Partnership rail -- partnership_allowed, the stored
    account number, is_active and partnership_new_business_allowed. The version
    of these tests written for the previous rail built its states from a BankAccount
    row, which does not exist here; the states below are the Partnership
    equivalents, not a translation of that fixture.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(
            username="gatesaver", phone="08010000009", email="gate@zitch.test",
            first_name="Sade", last_name="Okoro", tier=1,
            email_verified=True, phone_verified=True, bvn_verified=True,
        )
        self.user.set_transaction_pin("1234")
        self.user.save()
        self.token = AccessToken.issue(self.user).key
        self.wallet = get_or_create_wallet(self.user)
        credit(self.user, Decimal("40000"), "Test top-up")

    def _lock(self, amount="10000", days=30):
        from .services import lock
        return lock(self.user, Decimal(amount), days)

    def _plans_and_balance(self):
        self.wallet.refresh_from_db()
        return FixedSave.objects.count(), self.wallet.balance

    def test_an_account_awaiting_balance_review_cannot_lock_savings(self):
        """An unarchived UnsettledFunding row is what review means here."""
        from wallet.models import UnsettledFunding

        UnsettledFunding.objects.create(
            user=self.user, amount=Decimal("5000"),
            destination_digest="a" * 64, operator_reference="LEGACY-1")
        from wallet.services import LimitExceeded
        with self.assertRaises(LimitExceeded) as ctx:
            self._lock()
        self.assertIn("balance review", str(ctx.exception).lower())
        self.assertEqual(self._plans_and_balance(), (0, Decimal("40000.00")))

    def test_a_retired_destination_on_the_wallet_cannot_lock_savings(self):
        """A stored number the Partnership rail will not accept means review,
        and must not be spendable through savings either."""
        self.wallet.account_number = "7110000001"      # retired prefix
        self.wallet.save(update_fields=["account_number"])
        from wallet.services import LimitExceeded
        with self.assertRaises(LimitExceeded) as ctx:
            self._lock()
        self.assertIn("balance review", str(ctx.exception).lower())
        self.assertEqual(self._plans_and_balance(), (0, Decimal("40000.00")))

    def test_a_deactivated_account_cannot_lock_savings(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        from wallet.services import LimitExceeded
        with self.assertRaises(LimitExceeded):
            self._lock()
        self.assertEqual(self._plans_and_balance(), (0, Decimal("40000.00")))

    def test_a_lock_above_the_tier_ceiling_is_refused(self):
        """The ceiling applies to a lock exactly as it applies to a transfer."""
        from accounts.models import User as U
        from wallet.services import LimitExceeded

        ceiling = U.TIER_LIMITS[self.user.tier]
        credit(self.user, ceiling, "Headroom")
        with self.assertRaises(LimitExceeded) as ctx:
            self._lock(amount=str(ceiling + Decimal("1")))
        self.assertIn(f"Tier {self.user.tier}", str(ctx.exception))
        self.assertEqual(FixedSave.objects.count(), 0)

    def test_the_available_balance_caps_a_lock_not_the_stored_one(self):
        """A wallet can hold more than it may spend. The lock must measure the
        spendable figure, or savings becomes the way to move held funds."""
        from unittest.mock import patch
        from wallet.services import InsufficientFunds

        with patch("savings.services.customer_spendable_balance",
                   return_value=Decimal("1000")):
            with self.assertRaises(InsufficientFunds):
                self._lock(amount="20000")
        self.assertEqual(self._plans_and_balance(), (0, Decimal("40000.00")))

    def test_a_lock_within_every_cap_still_succeeds(self):
        """The gate must not refuse an ordinary lock -- otherwise the suite above
        would pass with savings simply broken."""
        plan = self._lock(amount="10000")
        self.assertEqual(plan.principal, Decimal("10000"))
        self.assertEqual(self._plans_and_balance(), (1, Decimal("30000.00")))

    def test_create_answers_403_limit_exceeded_and_names_the_reason(self):
        """The HTTP surface reports the refusal rather than a 500."""
        from wallet.models import UnsettledFunding

        UnsettledFunding.objects.create(
            user=self.user, amount=Decimal("5000"),
            destination_digest="b" * 64, operator_reference="LEGACY-2")
        res = self.client.post(
            "/api/savings/create/",
            data=json.dumps({"access_token": self.token, "amount": "10000",
                             "days": 30, "transaction_pin": "1234",
                             "idempotency_key": "gate-403"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 403)
        body = res.json()
        self.assertEqual(body.get("code"), "limit_exceeded")
        self.assertIn("balance review", json.dumps(body).lower())
        self.assertEqual(FixedSave.objects.count(), 0)

    def test_the_label_the_caps_are_checked_against_is_the_label_written(self):
        """One constant, because a spend must count toward the bucket it was
        measured against. Two literals would drift."""
        from wallet.models import Transaction
        from .services import LOCK_SERVICE

        self._lock(amount="10000")
        row = Transaction.objects.filter(direction=Transaction.OUT).latest("id")
        self.assertEqual(row.service, LOCK_SERVICE)

    def test_savings_is_deliberately_in_no_daily_bucket(self):
        """Locking moves money between the customer's own pockets, so it is not
        metered against the daily send caps. Asserted so that a future bucket
        added without thinking is a failing test rather than a surprise cap."""
        from common.http import daily_kind_for
        from .services import LOCK_SERVICE

        # "" is this function's no-bucket answer, not None.
        self.assertEqual(daily_kind_for(LOCK_SERVICE), "")
