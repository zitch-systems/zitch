"""Tests for the wallet core: balance, history, Monnify funding (idempotent),
Zitch-to-Zitch transfer, and the tier / face-verification send limits.

All run in MOCK provider mode (no keys), so funding settles automatically.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction as db_transaction
from django.test import Client, TestCase

from accounts.models import AccessToken

from .forex import FxError, create_fx_quote
from .models import FundingIntent, Transaction, Wallet
from .services import credit, credit_kora_virtual_account_funding, get_or_create_wallet

User = get_user_model()


def make_user(phone, email, pin="1234", balance="0", tier=1):
    u = User.objects.create(username=phone, phone=phone, email=email,
                            first_name="Ada", last_name="Eze", tier=tier)
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

    # --- funding (idempotency is the whole point) ---
    def test_fund_verify_credits_once(self):
        _, init = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "5000"})
        ref = init["reference"]
        self.assertTrue(FundingIntent.objects.get(reference=ref).status == FundingIntent.PENDING)

        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("25000"))
        # A duplicate verify (app retry) must not double-credit.
        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("25000"))
        self.assertTrue(FundingIntent.objects.get(reference=ref).credited)

    def test_fund_webhook_credits_once_and_dedupes_with_verify(self):
        _, init = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "7500"})
        ref = init["reference"]
        event = {"event": "charge.success", "data": {"reference": ref, "amount": 7500}}
        # Webhook credits (mock signature accepted).
        r1 = self.client.post("/api/fund/webhook/", data=json.dumps(event),
                              content_type="application/json", HTTP_X_KORAPAY_SIGNATURE="mock")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self.balance(self.user), Decimal("27500"))
        # Webhook AND the app's verify racing: still only one credit.
        self.client.post("/api/fund/webhook/", data=json.dumps(event),
                         content_type="application/json", HTTP_X_KORAPAY_SIGNATURE="mock")
        self.post("/api/fund/verify/", {"access_token": self.token, "reference": ref})
        self.assertEqual(self.balance(self.user), Decimal("27500"))

    def test_fund_rejects_below_minimum(self):
        res, _ = self.post("/api/fund/initialize/", {"access_token": self.token, "amount": "50"})
        self.assertEqual(res.status_code, 400)

    # --- dedicated account via Kora onboarding (BVN) ---
    def test_account_create_via_bvn_onboarding(self):
        res, body = self.post("/api/wallet/account/create/", {"access_token": self.token, "bvn": "22211100099"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertTrue(body["account_number"])
        # The one BVN step provisions the account AND records BVN verification, but
        # the user stays Tier 0 until NIN is also verified (Tier 1 = BVN + NIN).
        self.assertTrue(body["bvn_verified"])
        self.assertEqual(body["tier"], 0)
        u = User.objects.get(pk=self.user.pk)
        self.assertTrue(u.bvn_verified)
        self.assertEqual(u.tier, 0)
        self.assertEqual(u.bvn_last4, "0099")          # stored hashed, last-4 only
        self.assertFalse(hasattr(u, "bvn"))            # never the raw number
        # Idempotent: a second call returns the same account, never a second mint.
        res2, body2 = self.post("/api/wallet/account/create/", {"access_token": self.token, "bvn": "22211100099"})
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(body2["account_number"], body["account_number"])

    def test_account_create_rejected_when_reserve_fails(self):
        """When Kora rejects the virtual-account onboarding (e.g. a BVN/name
        mismatch), no account is minted and the user stays tier 1 / unverified."""
        with patch("utility.kora.create_virtual_account",
                   return_value={"success": False, "message": "BVN/name mismatch"}), \
                patch("utility.kora.get_virtual_account", return_value={"success": False}):
            res, _ = self.post("/api/wallet/account/create/", {"access_token": self.token, "bvn": "22211100099"})
        self.assertEqual(res.status_code, 502)
        u = User.objects.get(pk=self.user.pk)
        self.assertFalse(u.bvn_verified)
        self.assertEqual(u.tier, 1)
        self.assertFalse(get_or_create_wallet(u).account_number)

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

    def test_ensure_reserved_account_is_idempotent(self):
        from .services import ensure_reserved_account

        w1 = ensure_reserved_account(self.user, bvn="22200000001")
        self.assertTrue(w1.account_number)
        self.assertTrue(w1.account_reference)
        self.assertTrue(w1.bank_accounts)
        # A second call must not re-reserve / change the number.
        number = w1.account_number
        w2 = ensure_reserved_account(self.user, bvn="22200000001")
        self.assertEqual(w2.account_number, number)

    def test_bvn_verification_reserves_and_balance_surfaces_it(self):
        res, body = self.post("/api/kyc/bvn/", {"access_token": self.token, "bvn": "22200000001"})
        self.assertEqual(res.status_code, 200)
        wallet = get_or_create_wallet(self.user)
        self.assertTrue(wallet.account_number)
        # wallet_balance now carries the dedicated account for the app to show.
        res, body = self.post("/api/wallet_balance/", {"access_token": self.token})
        self.assertEqual(body["account_number"], wallet.account_number)
        self.assertTrue(body["bank_name"])
        self.assertTrue(body["bank_accounts"])

    def test_webhook_credits_reserved_account_once(self):
        from .services import ensure_reserved_account

        wallet = ensure_reserved_account(self.user, bvn="22200000001")
        event = {"event": "charge.success", "data": {
            "reference": "KORA-TX-RSV001", "amount": 5000,
            "account_reference": wallet.account_reference,
            "virtual_bank_account_details": {"account_number": wallet.account_number},
        }}
        body = json.dumps(event)
        r1 = self.client.post("/api/fund/webhook/", data=body, content_type="application/json",
                              HTTP_X_KORAPAY_SIGNATURE="mock")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(self.bal(), Decimal("5000"))
        # Redelivered webhook (same reference) must not double-credit.
        self.client.post("/api/fund/webhook/", data=body, content_type="application/json",
                         HTTP_X_KORAPAY_SIGNATURE="mock")
        self.assertEqual(self.bal(), Decimal("5000"))

    def test_webhook_ignores_unknown_reserved_account(self):
        event = {"event": "charge.success", "data": {
            "reference": "KORA-TX-RSV404", "amount": 5000,
            "account_reference": "ZITCH-WALLET-999999",
            "virtual_bank_account_details": {"account_number": "0000000000"},
        }}
        r = self.client.post("/api/fund/webhook/", data=json.dumps(event),
                             content_type="application/json", HTTP_X_KORAPAY_SIGNATURE="mock")
        self.assertEqual(r.status_code, 200)  # accepted so Kora stops retrying
        self.assertEqual(self.bal(), Decimal("0"))  # but nothing credited

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

    def test_reserved_funding_credits_correct_wallet_by_account_number(self):
        u, _ = make_user("08077700003", "rc@zitch.test")
        w = get_or_create_wallet(u)
        w.account_number = "9921000099"
        w.account_reference = "ZITCH-WALLET-X"
        w.save()
        data = {
            "account_reference": "does-not-match",
            "virtual_bank_account_details": {"account_number": "9921000099"},
            "reference": "KORA|TEST|001",
            "amount": "5000.00",
        }
        txn = credit_kora_virtual_account_funding(data)
        self.assertIsNotNone(txn)
        self.assertEqual(get_or_create_wallet(u).balance, Decimal("5000.00"))
        # Redelivered webhook (same reference) must not double-credit.
        self.assertIsNone(credit_kora_virtual_account_funding(data))
        self.assertEqual(get_or_create_wallet(u).balance, Decimal("5000.00"))

    def test_reserved_funding_unknown_account_is_ignored(self):
        data = {
            "account_reference": "nobody",
            "virtual_bank_account_details": {"account_number": "0000000000"},
            "reference": "KORA|TEST|002",
            "amount": "1000.00",
        }
        self.assertIsNone(credit_kora_virtual_account_funding(data))
