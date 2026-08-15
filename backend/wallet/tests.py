"""Tests for the wallet core: balance, history, Wema funding (idempotent),
Zitch-to-Zitch transfer, and the tier / face-verification send limits.

All run in MOCK provider mode (no keys), so funding settles automatically.
"""
import io
import json
import zipfile
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction as db_transaction
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AccessToken
from common.http import unverified_error

from .forex import FxError, create_fx_quote
from .models import CurrencyWallet, FundingIntent, Transaction, Wallet
from .services import (LimitExceeded, credit, debit, get_or_create_wallet,
                       settle_funding, settle_reserved_funding)

User = get_user_model()


def make_user(phone, email, pin="1234", balance="0", tier=1, identity_verified=True):
    """Contact channels are always verified — the app earns `phone_verified` at
    signup and email is a precondition for anything above the floor, so a test
    account without them is not a state worth modelling.

    `identity_verified` covers BVN/NIN, which a first spend now also requires. It
    defaults on because most tests are about something else and would otherwise
    be refused before reaching it; tests that exercise *earning* identity — the
    KYC ladder, bank provisioning, the portal review queue — pass False so the
    user starts where a real one does.
    """
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=tier,
                            email_verified=True, phone_verified=True,
                            bvn_verified=identity_verified,
                            nin_verified=identity_verified)
    u.set_transaction_pin(pin)
    u.save()
    get_or_create_wallet(u)
    if Decimal(balance) > 0:
        credit(u, Decimal(balance), "Seed")
    return u, AccessToken.issue(u).key


class FxLimitTests(TestCase):
    """Currency conversion must enforce the same KYC tier / large-transfer face
    gate as every other money-out flow (regression for the FX limit bypass)."""

    def test_tier1_over_cap_blocked(self):
        user, _ = make_user("08055500001", "fxa@zitch.test", balance="200000", tier=1)
        with self.assertRaises(FxError):  # ₦60k > ₦50k tier-1 cap
            create_fx_quote(user, "NGN", "USD", Decimal("60000"))

    def test_large_transfer_needs_face(self):
        # Tier-3 cap is ₦5M, but >= ₦100k requires face verification (not set here).
        user, _ = make_user("08055500002", "fxb@zitch.test", balance="500000", tier=3)
        with self.assertRaises(FxError):
            create_fx_quote(user, "NGN", "USD", Decimal("150000"))

    def test_within_limit_allowed(self):
        user, _ = make_user("08055500003", "fxc@zitch.test", balance="200000", tier=1)
        q = create_fx_quote(user, "NGN", "USD", Decimal("10000"))
        self.assertEqual(q.to_currency, "USD")


class WalletTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self, user):
        return get_or_create_wallet(user).balance

    def test_balance_endpoint(self):
        res, body = self.post("/api/wallet_balance/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(Decimal(body["wallet"]), Decimal("20000"))
        self.assertEqual(body["user_first_name"], "Ada")

    def test_balance_requires_valid_token(self):
        res, _ = self.post("/api/wallet_balance/", {"access_token": "nope"})
        self.assertEqual(res.status_code, 401)

    def test_bearer_header_authenticates(self):
        # Token via Authorization: Bearer header, no body token at all.
        res = self.client.post("/api/wallet_balance/", data=json.dumps({}),
                               content_type="application/json",
                               HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(Decimal(res.json()["wallet"]), Decimal("20000"))

    def test_bearer_header_takes_precedence_over_body(self):
        # Valid header beats a bogus body token (header is preferred).
        res = self.client.post("/api/wallet_balance/", data=json.dumps({"access_token": "bogus"}),
                               content_type="application/json",
                               HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(res.status_code, 200)

    def test_history_returns_authoritative_direction(self):
        """History must carry a `direction` field — the app keys inflow/outflow
        off it. The label regex alone misclassifies credits like 'Wallet top-up'
        and 'Transfer from …', so the backend value is the source of truth."""
        credit(self.user, Decimal("5000"), "Wallet top-up")  # an inflow
        make_user("08020000002", "bob@zitch.test")
        self.post("/api/transfer/send/", {  # an outflow
            "access_token": self.token, "identifier": "08020000002",
            "amount": "1000", "transaction_pin": "1234",
        })
        _, body = self.post("/api/user-transaction-history/", {"access_token": self.token})
        dirs = {r["service"]: r["direction"] for r in body["all_site_transactions"]}
        self.assertEqual(dirs.get("Wallet top-up"), "in")
        self.assertTrue(any(s.startswith("Transfer to") and d == "out" for s, d in dirs.items()))

    def test_fund_rejects_below_minimum(self):
        res, _ = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "50"})
        self.assertEqual(res.status_code, 400)

    def test_account_create_requires_valid_id(self):
        res, _ = self.post("/api/wallet/account/create/", {"access_token": self.token, "bvn": "123"})
        self.assertEqual(res.status_code, 400)

    # --- transfer ---
    def test_transfer_moves_funds_atomically(self):
        bob, _ = make_user("08020000002", "bob@zitch.test")
        res, body = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "5000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(self.user), Decimal("15000"))
        self.assertEqual(self.balance(bob), Decimal("5000"))

    def test_transfer_to_recipient_without_wallet_row(self):
        """A recipient only gets a wallet when they first authenticate, so an
        admin-created/seeded user can exist with no wallet row. Sending to them
        must mint one and move the money, not 500 with a KeyError."""
        bob = User.objects.create(username="08020000099", phone="08020000099",
                                  email="bob2@zitch.test")
        self.assertFalse(Wallet.objects.filter(user=bob).exists())
        res, body = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000099",
            "amount": "5000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(self.user), Decimal("15000"))
        self.assertEqual(self.balance(bob), Decimal("5000"))

    def test_transfer_rejects_wrong_pin(self):
        make_user("08020000002", "bob@zitch.test")
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "5000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(self.user), Decimal("20000"))

    # --- ledger immutability ---
    def test_settled_ledger_row_amount_is_immutable(self):
        txn = credit(self.user, Decimal("100"), "Seed credit")
        txn.amount = Decimal("999999")
        with self.assertRaises(ValueError):
            txn.save()
        # status/meta updates (settlement, flagging) remain allowed.
        txn.refresh_from_db()
        txn.transaction_status = Transaction.FAILED
        txn.save()  # should not raise
        self.assertEqual(Transaction.objects.get(pk=txn.pk).amount, Decimal("100"))

    def test_transfer_rejects_insufficient_funds(self):
        make_user("08020000002", "bob@zitch.test")
        # 30,000 is within the tier-1 limit (50k) but above the 20k balance,
        # so this exercises the insufficient-funds path, not the limit guard.
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08020000002",
            "amount": "30000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 402)
        self.assertEqual(self.balance(self.user), Decimal("20000"))

    def test_cannot_transfer_to_self(self):
        res, _ = self.post("/api/transfer/send/", {
            "access_token": self.token, "identifier": "08010000001",
            "amount": "100", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)

    # --- tier / face limits (check_send_limits) ---
    def test_transfer_blocked_over_tier_limit(self):
        rich, token = make_user("08030000003", "rich@zitch.test", balance="500000", tier=1)
        make_user("08040000004", "x@zitch.test")
        # Tier 1 limit is 50,000.
        res = self.client.post("/api/transfer/send/", data=json.dumps({
            "access_token": token, "identifier": "08040000004",
            "amount": "60000", "transaction_pin": "1234",
        }), content_type="application/json")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["code"], "limit_exceeded")

    def test_large_transfer_requires_server_side_face_verification(self):
        u, token = make_user("08030000003", "rich@zitch.test", balance="500000", tier=2)
        # Tier 2 (₦200k cap) allows ₦150k, but a >=₦100k transfer still needs the
        # server-side face flag. Construct the (now-rare) Tier-2-without-face state
        # directly — face is normally a Tier-2 requirement, so set flags explicitly.
        User.objects.filter(pk=u.pk).update(
            bvn_verified=True, nin_verified=True, address_verified=True,
            face_verified=False, tier=2)
        make_user("08040000004", "x@zitch.test")
        body = {"access_token": token, "identifier": "08040000004",
                "amount": "150000", "transaction_pin": "1234"}
        # >= 100,000 needs face verification.
        res, b = self.post("/api/transfer/send/", body)
        self.assertEqual(res.status_code, 403)
        self.assertEqual(b["code"], "face_required")
        # A client-asserted face_confirmed must NOT bypass the gate.
        res, _ = self.post("/api/transfer/send/", {**body, "face_confirmed": True})
        self.assertEqual(res.status_code, 403)
        # Durable, server-side face verification clears it (set directly so the
        # tier stays 2 — going through /kyc/face/ would recompute it).
        User.objects.filter(pk=u.pk).update(face_verified=True)
        res, b = self.post("/api/transfer/send/", body)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(b["success"])


class ReservedAccountTests(TestCase):
    """Dedicated (reserved) virtual account: minted at KYC, surfaced on the wallet,
    and credited by the funding webhook — idempotently."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def bal(self):
        return get_or_create_wallet(self.user).balance

    def test_wallet_account_endpoint_is_a_fast_read_without_provisioning(self):
        # The read endpoint never provisions on load (that needs the BVN, which we
        # don't store, and a slow provider call would hang the page). A verified
        # user with no number yet just gets an empty one back, fast.
        self.user.bvn_verified = True
        self.user.save(update_fields=["bvn_verified"])
        res, body = self.post("/api/wallet/account/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["account_number"], "")
        # Once provisioned, the read returns the stored account.
        w = get_or_create_wallet(self.user)
        w.account_number, w.bank_name, w.account_name = "7012345678", "Wema Bank", "Ada Eze"
        w.save(update_fields=["account_number", "bank_name", "account_name"])
        _, body2 = self.post("/api/wallet/account/", {"access_token": self.token})
        self.assertEqual(body2["account_number"], "7012345678")
        self.assertEqual(body2["bank_name"], "Wema Bank")

    def test_wallet_account_returns_registered_holder_name(self):
        # The read always carries the customer's registered legal name so the
        # Add-money screen can show whose account this is — even before it's
        # provisioned, or if a provider response omits the holder name.
        res, body = self.post("/api/wallet/account/", {"access_token": self.token})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["holder_name"], self.user.get_full_name())  # "Ada Eze"


class LedgerConstraintTests(TestCase):
    """DB-level guards that back up the service-layer money checks."""

    def test_wallet_balance_cannot_go_negative(self):
        user, _ = make_user("08010000001", "a@zitch.test", balance="100")
        wallet = get_or_create_wallet(user)
        wallet.balance = Decimal("-1")
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            wallet.save()

    def test_transaction_amount_must_be_positive(self):
        user, _ = make_user("08020000002", "b@zitch.test")
        with self.assertRaises(IntegrityError), db_transaction.atomic():
            Transaction.objects.create(
                user=user, service="bad", amount=Decimal("0"),
                direction=Transaction.OUT, reference="ZBAD000001",
            )


class IdempotencyTests(TestCase):
    """A spend retried with the same idempotency_key debits / charges once."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="20000")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def bal(self):
        return get_or_create_wallet(self.user).balance

    def test_repeated_key_debits_airtime_once(self):
        body = {"access_token": self.token, "amount": "1000", "network": "1",
                "phone": "08010000001", "transaction_pin": "1234", "idempotency_key": "k-air-1"}
        r1, _ = self.post("/api/utility/buyairtime/", body)
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self.bal(), Decimal("19000"))
        # Same key (client retry / double-fire): replay, not a second debit.
        r2, b2 = self.post("/api/utility/buyairtime/", body)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(b2.get("duplicate"))
        self.assertEqual(self.bal(), Decimal("19000"))  # would be 18000 without the guard
        self.assertEqual(Transaction.objects.filter(user=self.user, service__startswith="Airtime").count(), 1)
        # A different key is a genuinely new purchase.
        self.post("/api/utility/buyairtime/", {**body, "idempotency_key": "k-air-2"})
        self.assertEqual(self.bal(), Decimal("18000"))

    def test_repeated_key_transfers_once(self):
        make_user("08020000002", "bob@zitch.test")
        body = {"access_token": self.token, "identifier": "08020000002",
                "amount": "5000", "transaction_pin": "1234", "idempotency_key": "k-trf-1"}
        self.post("/api/transfer/send/", body)
        self.assertEqual(self.bal(), Decimal("15000"))
        _, b2 = self.post("/api/transfer/send/", body)  # retry
        self.assertEqual(self.bal(), Decimal("15000"))  # not 10000
        self.assertTrue(b2.get("duplicate"))

    def test_debit_duplicate_key_raises_and_rolls_back(self):
        """The DB unique constraint backs up the pre-check against a real race."""
        from wallet.services import DuplicateTransaction, debit
        debit(self.user, Decimal("100"), "X", idempotency_key="k-raw-1")
        with self.assertRaises(DuplicateTransaction):
            debit(self.user, Decimal("100"), "X", idempotency_key="k-raw-1")
        self.assertEqual(self.bal(), Decimal("19900"))  # the second debit rolled back


class ReservedFundingTests(TestCase):
    """Reserved (virtual) account funding: a wallet maps 1:1 to its account, and
    an inbound transfer credits the right wallet exactly once."""

    def test_account_number_must_be_unique(self):
        a, _ = make_user("08077700001", "ra@zitch.test")
        b, _ = make_user("08077700002", "rb@zitch.test")
        wa = get_or_create_wallet(a)
        wa.account_number = "9921000001"
        wa.save()
        wb = get_or_create_wallet(b)
        wb.account_number = "9921000001"
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                wb.save()

    def test_reserved_funding_credits_once_keyed_on_reference(self):
        u, _ = make_user("08077700003", "rc@zitch.test")
        get_or_create_wallet(u)
        txn = settle_reserved_funding("MNFY|TEST|001", "5000.00", u)
        self.assertIsNotNone(txn)
        self.assertEqual(get_or_create_wallet(u).balance, Decimal("5000.00"))
        # Redelivered webhook (same reference) must not double-credit.
        self.assertIsNone(settle_reserved_funding("MNFY|TEST|001", "5000.00", u))
        self.assertEqual(get_or_create_wallet(u).balance, Decimal("5000.00"))

    def test_reserved_funding_incomplete_input_is_ignored(self):
        u, _ = make_user("08077700004", "rc2@zitch.test")
        get_or_create_wallet(u)
        self.assertIsNone(settle_reserved_funding("", "1000.00", u))
        self.assertIsNone(settle_reserved_funding("MNFY|TEST|002", None, u))


class FundVerifyOwnershipTests(TestCase):
    """/api/fund/verify/ must not let a caller act on another user's funding ref."""

    def test_fund_verify_rejects_another_users_reference(self):
        a, _ = make_user("08088800001", "fa@zitch.test")
        _, tok_b = make_user("08088800002", "fb@zitch.test")
        FundingIntent.objects.create(user=a, reference="ZPAYOWN0001", amount=Decimal("5000"))
        res = Client().post(
            "/api/fund/verify/",
            data=json.dumps({"access_token": tok_b, "reference": "ZPAYOWN0001"}),
            content_type="application/json")
        self.assertEqual(res.status_code, 404)


class FundingSettlementAmountTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088800003", "fc@zitch.test")

    def _intent(self, reference):
        return FundingIntent.objects.create(
            user=self.user, reference=reference, amount=Decimal("5000"))

    def test_provider_cannot_credit_above_intent(self):
        self._intent("ZPAYCAP0001")
        settle_funding("ZPAYCAP0001", Decimal("9000"))
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("5000"))

    def test_invalid_provider_amount_does_not_credit(self):
        for index, amount in enumerate(("NaN", "not-money", Decimal("-1")), start=1):
            ref = f"ZPAYBAD000{index}"
            intent = self._intent(ref)
            self.assertIsNone(settle_funding(ref, amount))
            intent.refresh_from_db()
            self.assertFalse(intent.credited)
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("0"))


class FxLimitParityTests(TestCase):
    """FX was the one money-out path that skipped the compromised-account brake,
    and foreign->foreign conversions skipped every amount ceiling as well."""

    def setUp(self):
        self.user, self.token = make_user("08033330001", "fx@zitch.test", tier=1)
        credit(self.user, Decimal("500000"), "Seed")

    def test_velocity_brake_applies_to_conversion(self):
        with patch("wallet.forex.velocity_exceeded", return_value=True):
            with self.assertRaises(FxError) as ctx:
                create_fx_quote(self.user, "NGN", "USD", Decimal("1000"))
        self.assertIn("Too many transactions", str(ctx.exception.message))

    def test_foreign_to_foreign_is_capped_in_ngn_equivalent(self):
        # A tier-1 user's per-txn ceiling is denominated in NGN, so the foreign
        # sale is converted before comparison — otherwise "1000" of a strong
        # currency slips under a cap written for naira.
        CurrencyWallet.objects.create(user=self.user, currency="USD", balance=Decimal("9000"))
        with patch("wallet.forex._ngn_equivalent", return_value=Decimal("9000000")):
            with self.assertRaises(FxError):
                create_fx_quote(self.user, "USD", "GBP", Decimal("9000"))

    def test_an_unpriceable_foreign_pair_fails_closed(self):
        CurrencyWallet.objects.create(user=self.user, currency="USD", balance=Decimal("100"))
        with patch("wallet.forex._ngn_equivalent", return_value=None):
            with self.assertRaises(FxError) as ctx:
                create_fx_quote(self.user, "USD", "GBP", Decimal("100"))
        # Refused, not silently uncapped.
        self.assertIn("price", str(ctx.exception.message).lower())


class FirstSpendVerificationTests(TestCase):
    """Email, phone, BVN and NIN must all be proved before ANY money leaves an
    account. Together they are exactly Tier 1, but the refusal names the missing
    step rather than a tier number — "you are Tier 0" tells a customer nothing
    about what to do next."""

    def setUp(self):
        self.user, self.token = make_user("08044440001", "gate@zitch.test", balance="50000")

    def _unverify(self, *fields):
        for f in fields:
            setattr(self.user, f, False)
        self.user.save(update_fields=list(fields))

    def test_a_debit_is_refused_until_every_check_passes(self):
        self._unverify("bvn_verified")
        with self.assertRaises(LimitExceeded) as ctx:
            debit(self.user, Decimal("1000"), "transfer")
        self.assertIn("BVN", str(ctx.exception))
        # And the refusal is actionable on both surfaces.
        self.assertIn("8", str(ctx.exception))
        self.assertIn("app", str(ctx.exception).lower())

    def test_the_refusal_names_every_missing_check_not_just_the_first(self):
        self._unverify("bvn_verified", "email_verified")
        msg = unverified_error(self.user) or ""
        self.assertIn("BVN", msg)
        self.assertIn("email", msg)

    def test_an_unverified_nin_does_not_block_spending(self):
        """NIN has no unattended verification path until the Prembly lookup is
        confirmed live, so gating every first spend on it would strand every new
        customer behind a review queue. It still caps the tier."""
        self._unverify("nin_verified")
        self.assertIsNone(unverified_error(self.user))
        debit(self.user, Decimal("1000"), "transfer")   # does not raise

    def test_a_fully_verified_account_is_untouched(self):
        self.assertIsNone(unverified_error(self.user))
        debit(self.user, Decimal("1000"), "transfer")   # does not raise

    def test_funding_is_still_allowed_while_unverified(self):
        """Blocking a deposit would strand money in a NUBAN its owner cannot then
        use — and money arriving is how a customer gets to the point of verifying."""
        self._unverify("bvn_verified", "nin_verified")
        credit(self.user, Decimal("5000"), "Deposit")           # does not raise
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("55000"))

    def test_conversion_is_gated_too(self):
        """FX reaches the ledger through _move, not debit(), so it does not
        inherit spend_limit_error's gate and has to state it — otherwise an
        unverified account could still move value by converting it."""
        self._unverify("bvn_verified")
        with self.assertRaises(FxError) as ctx:
            create_fx_quote(self.user, "NGN", "USD", Decimal("1000"))
        self.assertIn("BVN", str(ctx.exception.message))


class SmsAlertFormatTests(TestCase):
    """Nigerian bank alerts share one shape, and customers read them by position
    rather than by reading them. Matching it means a Zitch alert is scanned the
    same way as the bank's own alert sitting directly above it."""

    def setUp(self):
        self.user, _ = make_user("08060000009", "alert@zitch.test", balance="10000")
        w = get_or_create_wallet(self.user)
        w.account_number = "0228565772"
        w.save(update_fields=["account_number"])

    def _alert(self, **kw):
        from wallet.alerts import _sms_alert

        with self.captureOnCommitCallbacks(execute=True):
            credit(self.user, Decimal("4300"), "Transfer from YUSUFF MUZZAMMIL")
        txn = Transaction.objects.filter(user=self.user).order_by("-created").first()
        return _sms_alert(txn, **kw), txn

    def test_it_follows_the_bank_layout_line_for_line(self):
        body, txn = self._alert()
        lines = body.split("\n")
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].startswith("CR:NGN 4,300.00"))
        self.assertEqual(lines[1], "Acct No:0228****72")     # bank's own masking
        self.assertTrue(lines[2].startswith("Desc :"))
        self.assertTrue(lines[3].startswith("Bal :"))
        self.assertRegex(lines[4], r"^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}$")

    def test_a_debit_reads_dr(self):
        from wallet.alerts import _sms_alert

        with self.captureOnCommitCallbacks(execute=True):
            debit(self.user, Decimal("2100"), "transfer", meta={"recipient_name": "LUCY OJOCHIDE"})
        txn = Transaction.objects.filter(user=self.user, direction=Transaction.OUT).first()
        self.assertTrue(_sms_alert(txn).startswith("DR:NGN 2,100.00"))

    def test_a_reversal_reads_cr_even_though_the_row_is_a_debit(self):
        """Calling money coming back a debit is the most alarming possible way to
        phrase good news."""
        from wallet.alerts import _sms_alert

        with self.captureOnCommitCallbacks(execute=True):
            debit(self.user, Decimal("500"), "transfer")
        txn = Transaction.objects.filter(user=self.user, direction=Transaction.OUT).first()
        body = _sms_alert(txn, reversal=True)
        self.assertTrue(body.startswith("CR:"))
        self.assertIn("REVERSAL-", body)

    def test_it_stays_inside_one_segment_by_trimming_the_description(self):
        """The balance and the timestamp are what the customer checks; a second
        segment costs a second message."""
        from wallet.alerts import _sms_alert

        with self.captureOnCommitCallbacks(execute=True):
            credit(self.user, Decimal("100"), "x" * 300)
        txn = Transaction.objects.filter(user=self.user).order_by("-created").first()
        body = _sms_alert(txn)
        self.assertLessEqual(len(body), 160)
        self.assertIn("Bal :", body)
        self.assertRegex(body.split("\n")[-1], r"^\d{2}-\d{2}-\d{4}")

    def test_it_stays_inside_the_gsm_7_alphabet(self):
        """One character outside GSM-7 forces the whole message into UCS-2, where
        a segment is 70 characters rather than 160 — so a single naira sign turns
        every alert into two billable messages."""
        from wallet.alerts import _sms_alert

        with self.captureOnCommitCallbacks(execute=True):
            credit(self.user, Decimal("4300"), "Transfer from YUSUFF")
        txn = Transaction.objects.filter(user=self.user).order_by("-created").first()
        body = _sms_alert(txn)
        self.assertNotIn("₦", body)
        self.assertIn("NGN ", body)
        body.encode("ascii")            # raises if anything non-GSM crept in

    def test_an_unreadable_wallet_never_breaks_the_alert(self):
        from wallet.alerts import _mask_account

        self.assertEqual(_mask_account(""), "—")
        self.assertEqual(_mask_account("0228565772"), "0228****72")


class EmailAlertBrandingTests(TestCase):
    """The alert arrived as five lines of bare text under a green "N" avatar —
    indistinguishable from phishing, which for a money alert is disqualifying.
    The HTML card carries the brand; the plain text stays as the fallback."""

    def setUp(self):
        self.user, _ = make_user("08060000011", "brand@zitch.test", balance="50000")
        w = get_or_create_wallet(self.user)
        w.account_number = "0228565772"
        w.save(update_fields=["account_number"])

    def _send(self):
        with patch("utility.providers.send_email",
                   return_value={"success": True}) as mail, \
             self.captureOnCommitCallbacks(execute=True):
            credit(self.user, Decimal("4300"), "Transfer from YUSUFF")
        return mail.call_args

    def test_the_html_body_rides_alongside_the_text_fallback(self):
        call = self._send()
        self.assertTrue(call.args[2])                        # plain text intact
        html = call.kwargs.get("html") or ""
        from common.emails import LOGO_URL

        self.assertIn(LOGO_URL, html)                        # the shared brand logo
        self.assertIn("+₦4,300.00", html)                    # signed amount
        self.assertIn("0228****72", html)                    # masked, never full
        self.assertNotIn("0228565772", html)
        self.assertIn("support@zitch.ng", html)

    def test_a_debit_renders_in_the_debit_colour_and_sign(self):
        from wallet.alerts import _email_alert_html

        with self.captureOnCommitCallbacks(execute=True):
            debit(self.user, Decimal("2300"), "transfer", meta={"recipient_name": "ADEYEMI WILLIAM"})
        txn = Transaction.objects.filter(user=self.user, direction=Transaction.OUT).first()
        html = _email_alert_html(txn)
        self.assertIn("#b8402f", html)
        self.assertIn("−₦2,300.00", html)
        self.assertIn("ADEYEMI WILLIAM", html)

    def test_an_unreadable_wallet_still_renders(self):
        from wallet.alerts import _email_alert_html

        with self.captureOnCommitCallbacks(execute=True):
            credit(self.user, Decimal("100"), "Deposit")
        txn = Transaction.objects.filter(user=self.user).order_by("-created").first()
        with patch("wallet.services.get_or_create_wallet", side_effect=RuntimeError("db")):
            html = _email_alert_html(txn)
        self.assertIn("—", html)
        self.assertIn("₦100.00", html)


class StatementRequestTests(TestCase):
    """The emailed PDF/Excel statement — the file a customer hands to a landlord
    or an embassy. What matters is that the range it claims is the range it
    contains, and that the address only appears when it was asked for."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000009", "ada@zitch.test", balance="20000")

    def post(self, payload):
        res = self.client.post("/api/wallet/statement/request/",
                               data=json.dumps({"access_token": self.token, **payload}),
                               content_type="application/json")
        return res, res.json()

    def test_rejects_an_unknown_file_type(self):
        res, body = self.post({"file_type": "docx"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("PDF", body["message"])

    def test_rejects_a_backwards_range(self):
        res, body = self.post({"from": "2026-08-14", "to": "2026-07-15", "file_type": "pdf"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("end date", body["message"].lower())

    def test_rejects_a_destination_that_is_not_an_email(self):
        res, _ = self.post({"file_type": "pdf", "email": "not-an-address"})
        self.assertEqual(res.status_code, 400)

    def test_pdf_is_attached_and_named_for_its_period(self):
        credit(self.user, Decimal("5000"), "Wallet top-up")
        with patch("utility.providers.send_email", return_value={"success": True}) as sent:
            res, body = self.post({"from": "2020-01-01", "to": "2030-01-01", "file_type": "pdf"})
        self.assertEqual(res.status_code, 200, body)
        attachment = sent.call_args.kwargs["attachments"][0]
        self.assertTrue(attachment["filename"].endswith(".pdf"))
        self.assertIn("2020-01-01", attachment["filename"])
        self.assertTrue(attachment["content"].startswith(b"%PDF-"))

    def test_excel_attachment_is_a_readable_workbook(self):
        credit(self.user, Decimal("5000"), "Wallet top-up")
        with patch("utility.providers.send_email", return_value={"success": True}) as sent:
            res, body = self.post({"from": "2020-01-01", "to": "2030-01-01", "file_type": "excel"})
        self.assertEqual(res.status_code, 200, body)
        attachment = sent.call_args.kwargs["attachments"][0]
        self.assertTrue(attachment["filename"].endswith(".xlsx"))
        with zipfile.ZipFile(io.BytesIO(attachment["content"])) as z:
            self.assertIsNone(z.testzip())
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())
            self.assertIn("Wallet top-up", z.read("xl/worksheets/sheet1.xml").decode())

    def test_the_range_bounds_what_the_file_contains(self):
        """The end DAY is inclusive. A statement 'to 14 Aug' that dropped
        everything after midnight on the 14th would read as missing money."""
        credit(self.user, Decimal("5000"), "Inside the window")
        txn = Transaction.objects.filter(user=self.user).order_by("-created").first()
        today = timezone.localtime(txn.created).date()
        day = today.strftime("%Y-%m-%d")

        # A single-day window whose start AND end are the transaction's own day
        # must contain it — the end day is inclusive, not exclusive.
        with patch("utility.providers.send_email", return_value={"success": True}) as sent:
            res, body = self.post({"from": day, "to": day, "file_type": "excel"})
        self.assertEqual(res.status_code, 200, body)
        self.assertGreaterEqual(body["count"], 1)
        sheet = sent.call_args.kwargs["attachments"][0]["content"]
        with zipfile.ZipFile(io.BytesIO(sheet)) as z:
            self.assertIn("Inside the window", z.read("xl/worksheets/sheet1.xml").decode())

        # …and a window that closes the day before must not.
        before = (today - timedelta(days=2)).strftime("%Y-%m-%d")
        yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        with patch("utility.providers.send_email", return_value={"success": True}) as sent:
            res, body = self.post({"from": before, "to": yesterday, "file_type": "excel"})
        self.assertEqual(res.status_code, 200, body)
        self.assertEqual(body["count"], 0)
        sheet = sent.call_args.kwargs["attachments"][0]["content"]
        with zipfile.ZipFile(io.BytesIO(sheet)) as z:
            self.assertNotIn("Inside the window", z.read("xl/worksheets/sheet1.xml").decode())

    def test_the_address_only_appears_when_it_was_asked_for(self):
        """A home address printed on every forwarded statement is a privacy leak,
        so the PDF must be materially different when the toggle is off."""
        self.user.address = "12 Marina Road, Lagos Island"
        self.user.save(update_fields=["address"])
        credit(self.user, Decimal("5000"), "Wallet top-up")
        with patch("utility.providers.send_email", return_value={"success": True}) as sent:
            self.post({"file_type": "pdf", "include_address": False})
            without = sent.call_args.kwargs["attachments"][0]["content"]
            self.post({"file_type": "pdf", "include_address": True})
            with_addr = sent.call_args.kwargs["attachments"][0]["content"]
        self.assertNotEqual(len(without), len(with_addr))

    def test_a_refused_send_is_reported_rather_than_claimed(self):
        with patch("utility.providers.send_email", return_value={"success": False}):
            res, body = self.post({"file_type": "pdf"})
        self.assertEqual(res.status_code, 502)
        self.assertFalse(body.get("success"))
