"""Tests for bank transfer (payout) + saved beneficiaries."""
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from wallet.models import Transaction
from wallet.services import get_or_create_wallet
from wallet.tests import make_user

from .models import AUTO_SAVE_AFTER, Bank, Beneficiary


class BankTransferTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="50000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def test_transfer_charge_returns_nip_fee(self):
        from decimal import Decimal
        with patch("transfers.views.payout_charge", return_value=Decimal("25.00")):
            res, body = self.post("/api/transfers/charge/", {"access_token": self.token, "amount": "20000"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["fee"], "25.00")

    def test_transfer_charge_defaults_to_zero_when_unavailable(self):
        with patch("transfers.views.payout_charge", return_value=None):
            res, body = self.post("/api/transfers/charge/", {"access_token": self.token, "amount": "20000"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["fee"], "0.00")

    def test_transfer_below_50_rejected(self):
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "ADEYEMI WILLIAM", "amount": "40", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("50", body.get("message", ""))
        self.assertEqual(self.balance(), Decimal("50000"))  # nothing moved

    def test_live_resolution_blocks_name_mismatch(self):
        # With a LIVE name enquiry, an account whose real holder differs from the
        # name the user confirmed must be BLOCKED â€” no debit. (Guards the reported
        # "account mismatch but the transfer went through".)
        with patch("transfers.views.payout_resolve_account",
                   return_value={"success": True, "name": "JANE SMITH"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 409)
        self.assertEqual(body.get("code"), "account_mismatch")
        self.assertEqual(body.get("resolved_name"), "JANE SMITH")
        self.assertEqual(self.balance(), Decimal("50000"))  # untouched

    def test_live_resolution_allows_matching_name(self):
        # The same holder (tolerant of word order) goes through and debits.
        with patch("transfers.views.payout_resolve_account",
                   return_value={"success": True, "name": "DOE JOHN"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("success") or body.get("pending"))
        self.assertEqual(self.balance(), Decimal("40000"))

    def test_banks_listed(self):
        res, body = self.post("/api/transfers/banks/", {})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(body["banks"][0]["code"], "gtb")
        # The picker renders a hosted logo (with a monogram fallback), so the
        # payload must always carry the field â€” blank when unset.
        self.assertIn("logo", body["banks"][0])

    def test_detect_sweeps_only_popular_banks_when_flagged(self):
        # With a `popular` set defined, auto-detect must not fan name-enquiries
        # out to every active bank (each probe is a paid provider call).
        from transfers.services import detect_account_banks
        popular = Bank.objects.create(code="acc", name="Access Bank", bank_code="044",
                                      color="#F68B1F", popular=True)
        probed = []

        def fake_resolve(acct, bank_code):
            probed.append(bank_code)
            return {"success": True, "name": "ADA EZE"}

        # detect_account_banks imports these inside the function, so patch the
        # source module, not transfers.services.
        with patch("utility.providers.payout_live", return_value=True), \
             patch("utility.providers.payout_resolve_account", side_effect=fake_resolve):
            matches = detect_account_banks("0000000001")
        self.assertEqual(probed, [popular.bank_code])  # gtb (not popular) skipped
        self.assertEqual([m["bank"] for m in matches], [popular.code])

    def test_resolve_requires_10_digits(self):
        res, _ = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "123", "bank": "gtb"})
        self.assertEqual(res.status_code, 400)

    def test_resolve_returns_name(self):
        res, body = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "0123456789", "bank": "gtb"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["name"])
        self.assertEqual(body["bank"], "gtb")  # echoes the bank back

    def test_resolve_auto_detects_bank_without_bank_param(self):
        # No `bank` supplied -> the server detects it (mock: the first active bank)
        # and returns the bank + name so the app can fill it in automatically.
        from django.core.cache import cache
        cache.clear()
        res, body = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "0123456789"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertTrue(body["name"])
        self.assertEqual(body["bank"], "gtb")
        self.assertEqual(len(body["matches"]), 1)

    def test_resolve_flags_mock_when_name_enquiry_not_live(self):
        # Without a live Wema name-enquiry rail the detection is a placeholder, so
        # the response must carry `mock: true` and the app won't auto-fill it as a
        # verified bank/holder (which looked like "mis-detection").
        from django.core.cache import cache
        cache.clear()
        res, body = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "0123456789"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("mock"))
        # An explicit-bank resolve is mock too in this mode.
        res2, body2 = self.post("/api/transfers/resolve/", {"access_token": self.token, "account_number": "0123456789", "bank": "gtb"})
        self.assertTrue(body2.get("mock"))

    def test_send_debits_and_saves_beneficiary(self):
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(self.balance(), Decimal("40000"))
        # Ledger row settled, beneficiary auto-saved & deduped.
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.SUCCESS)
        self.assertEqual(Beneficiary.objects.filter(user=self.user, account_number="0123456789").count(), 1)

    def test_send_dedupes_beneficiary_on_repeat(self):
        payload = {"access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                   "name": "John Doe", "amount": "5000", "transaction_pin": "1234"}
        self.post("/api/transfers/send/", payload)
        self.post("/api/transfers/send/", payload)
        self.assertEqual(Beneficiary.objects.filter(user=self.user, account_number="0123456789").count(), 1)

    def test_send_rejects_wrong_pin(self):
        res, _ = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "0000",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_send_rejects_insufficient(self):
        # Fresh user with 30k balance: 40k is within the tier-1 limit (50k) but
        # over balance, so this hits the insufficient-funds path, not the limit.
        poor, token = make_user("08090000009", "poor@zitch.test", balance="30000")
        res, _ = self.post("/api/transfers/send/", {
            "access_token": token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "40000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 402)
        self.assertEqual(get_or_create_wallet(poor).balance, Decimal("30000"))

    def test_send_enforces_tier_limit(self):
        # Tier 1 ceiling is 50,000; a 60,000 payout is blocked before any debit.
        res, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "60000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(body["code"], "limit_exceeded")
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_pending_payout_is_not_settled(self):
        # A rail that returns PENDING (queued / awaiting auth) must NOT be settled
        # as Successful â€” the row stays Pending (money debited, flagged for the
        # webhook) and the response says "processing", not "sent".
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "PENDING"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertFalse(body.get("success"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("40000"))  # debited, not refunded

    def test_pending_payout_excluded_from_vtu_reconcile_sweep(self):
        """Regression: a PENDING bank payout shares the reconcile+OUT shape with a
        VTU purchase, but must NOT be swept by the VTU.ng requery â€” that would
        query the wrong provider for a reference VTU.ng never saw (risking a wrong
        refund/settle). It is settled only by the disbursement webhook."""
        from datetime import timedelta

        from django.utils import timezone

        from wallet.services import credit, debit, pending_vtu_purchases

        # A PENDING bank payout (what execute_payout leaves on a rail 'PENDING').
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "PENDING"}):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        payout = Transaction.objects.get(reference=body["reference"])

        # A PENDING VTU purchase (reconcile, no bank meta) for contrast.
        credit(self.user, Decimal("1000"), "Seed")
        vtu = debit(self.user, Decimal("500"), "Airtime",
                    meta={"phone": "08010000001", "reconcile": True})

        cutoff = timezone.now() + timedelta(minutes=1)  # both are "old enough"
        swept = set(pending_vtu_purchases(cutoff).values_list("reference", flat=True))
        self.assertIn(vtu.reference, swept)         # VTU purchase is reconciled
        self.assertNotIn(payout.reference, swept)   # bank payout is not

        # And the cron leaves the payout untouched (never calls vtu_requery on it).
        with patch("utility.management.commands.reconcile_vtu.vtu_requery",
                   return_value={"success": True}) as mq:
            from django.core.management import call_command
            call_command("reconcile_vtu", "--older-than-minutes=0")
        requeried_refs = [c.args[0] for c in mq.call_args_list]
        self.assertNotIn(payout.reference, requeried_refs)
        payout.refresh_from_db()
        self.assertEqual(payout.transaction_status, Transaction.PENDING)  # still pending, not refunded
        self.assertEqual(self.balance(), Decimal("40500"))  # 50000 - 10000 payout + 1000 seed - 500 vtu

    def test_send_refunds_when_payout_fails(self):
        """If the payout provider declines, the wallet debit must be reversed."""
        with patch("transfers.services.payout_send",
                   return_value={"success": False, "message": "bank declined"}):
            res, _ = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.balance(), Decimal("50000"))  # fully refunded
        self.assertTrue(Transaction.objects.filter(user=self.user, transaction_status=Transaction.FAILED).exists())
        # A failed payout must not save the beneficiary.
        self.assertEqual(Beneficiary.objects.filter(user=self.user).count(), 0)

    def test_ambiguous_send_timeout_holds_debit_never_refunds(self):
        """CRITICAL: a network timeout on the non-idempotent disburse POST is
        AMBIGUOUS — Wema may already have paid the recipient. The debit MUST be
        HELD (PENDING + reconcile), never refunded. Refunding here drains the float
        (recipient keeps the money, sender made whole) and a retry double-pays. The
        reconcile flag then lets reconcile_wema settle/reverse it on the true
        outcome."""
        with patch("transfers.services.payout_send",
                   return_value={"success": False, "pending": True, "status": "pending",
                                 "message": "Transfer is processing"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        self.assertFalse(body.get("success"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)  # held, not settled
        self.assertTrue(txn.meta.get("reconcile"))                     # sweepable
        self.assertEqual(self.balance(), Decimal("40000"))             # debited, NOT refunded
        self.assertFalse(
            Transaction.objects.filter(user=self.user, transaction_status=Transaction.FAILED).exists())

    def test_accepted_unknown_status_holds_debit_and_reports_pending(self):
        """CRITICAL: a payout Wema ACCEPTED but reported with a status this code
        doesn't recognise must be treated exactly like PENDING — debit held,
        response pending — not as a failure. Misclassifying it refunded an
        in-flight transfer (double-spend) and showed the user "Error / success"."""
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "QUEUED_AT_SWITCH", "reference": "X"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(body.get("pending"))
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.PENDING)
        self.assertTrue(txn.meta.get("reconcile"))
        self.assertEqual(self.balance(), Decimal("40000"))  # held, not refunded

    def test_explicit_failed_status_refunds_even_when_envelope_succeeded(self):
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "DECLINED",
                                 "message": "Transaction declined"}):
            res, _ = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_provider_success_echo_is_never_shown_as_the_error(self):
        """A definitive provider rejection whose message is the request-level
        "success" echo must not reach the app as the error text (the
        "Error / success" dialog) — the view substitutes a real sentence."""
        with patch("transfers.services.payout_send",
                   return_value={"success": False, "message": "success"}):
            res, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertEqual(res.status_code, 502)
        self.assertNotEqual(body["message"].strip().lower(), "success")
        self.assertEqual(self.balance(), Decimal("50000"))  # definitive -> refunded

    def test_reconcile_flag_committed_before_provider_call(self):
        """A crash between the debit commit and the settle write must leave a row the
        payout sweep (meta__reconcile=True) can still find. Assert the committed
        PENDING row already carries the flag at the moment payout_send is invoked —
        i.e. it is pre-flagged with the debit, not after the call."""
        seen = {}

        def spy(*args, **kwargs):
            row = Transaction.objects.get(reference=args[1])  # args[1] == reference
            seen["reconcile"] = (row.meta or {}).get("reconcile")
            seen["status"] = row.transaction_status
            return {"success": True, "status": "success"}

        with patch("transfers.services.payout_send", side_effect=spy):
            self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        self.assertTrue(seen["reconcile"])                     # flagged BEFORE the send
        self.assertEqual(seen["status"], Transaction.PENDING)

    def test_delivered_payout_clears_reconcile_flag(self):
        """A confirmed-delivered payout is settled AND its reconcile flag cleared, so
        the reconciler never needlessly requeries a completed transfer."""
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        txn = Transaction.objects.get(reference=body["reference"])
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)
        self.assertNotIn("reconcile", txn.meta or {})


class DemoSourceAccountTests(TestCase):
    """A NUBAN minted while the payout rail was MOCKED exists nowhere at the bank,
    but stays on the wallet once live keys are set — and it is what we send as the
    payout's sourceAccountNumber. The rail rejects it and blames "the account
    number", which reads as the recipient's."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000009", "demo@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")
        wallet = get_or_create_wallet(self.user)
        wallet.account_number = "0112345678"
        wallet.bank_name = "Wema Bank (demo)"      # the mock rail's stamp
        wallet.save(update_fields=["account_number", "bank_name"])

    def send(self):
        res = self.client.post("/api/transfers/send/", data=json.dumps({
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
        }), content_type="application/json")
        return res, res.json()

    @patch("transfers.services.payout_live", create=True, return_value=True)
    @patch("utility.providers.payout_live", return_value=True)
    def test_a_live_payout_is_refused_before_any_debit(self, *_):
        before = get_or_create_wallet(self.user).balance
        with patch("transfers.services.payout_send") as send:
            res, body = self.send()
        self.assertFalse(body.get("success"))
        send.assert_not_called()                                   # never reached the rail
        self.assertEqual(get_or_create_wallet(self.user).balance, before)
        # No outbound row at all — not even a debit-then-refund pair, which is what
        # the user would otherwise watch happen on every attempt.
        self.assertEqual(
            Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count(), 0)
        self.assertIn("test mode", body["message"])

    @patch("utility.providers.payout_live", return_value=False)
    def test_mock_mode_still_works_for_local_and_test_use(self, _live):
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            _, body = self.send()
        self.assertTrue(body.get("success"))


class ClearDemoAccountAdminTests(TestCase):
    """provision_wema_account refuses to replace an existing NUBAN, so a wallet
    stamped with a test-mode one can never be issued a real one without this."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from wallet.admin import WalletAdmin
        from wallet.models import Wallet

        self.admin = WalletAdmin(Wallet, AdminSite())
        self.messages = []
        self.admin.message_user = lambda req, msg, level=None: self.messages.append(msg)

    def _wallet(self, phone, bank_name):
        user, _ = make_user(phone, f"{phone}@zitch.test", balance="100")
        wallet = get_or_create_wallet(user)
        wallet.account_number = f"01{phone[-8:]}"
        wallet.bank_name = bank_name
        wallet.account_reference = f"ref-{phone}"
        wallet.save(update_fields=["account_number", "bank_name", "account_reference"])
        return wallet

    def test_clears_a_demo_account_but_keeps_the_balance(self):
        from wallet.models import Wallet

        wallet = self._wallet("08010000021", "Wema Bank (demo)")
        self.admin.clear_demo_account(None, Wallet.objects.filter(pk=wallet.pk))
        wallet.refresh_from_db()
        self.assertEqual(wallet.account_number, "")
        self.assertEqual(wallet.account_reference, "")
        self.assertEqual(wallet.balance, Decimal("100.00"))

    def test_never_clears_a_real_account(self):
        from wallet.models import Wallet

        wallet = self._wallet("08010000022", "Wema Bank")
        self.admin.clear_demo_account(None, Wallet.objects.filter(pk=wallet.pk))
        wallet.refresh_from_db()
        self.assertEqual(wallet.account_number, "0110000022")
        self.assertIn("issued by the real bank", " ".join(self.messages))


class PayoutFailureIsRecordedTests(TestCase):
    """A rejected payout used to leave nothing behind but a Failed row: the rail's
    reason went to the user and the log and was then lost. On a deploy with no
    shell and no log access, every failed transfer looked identical."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000031", "why@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")

    def test_the_rails_reason_is_kept_on_the_ledger_row(self):
        reason = "Debit account is restricted"
        with patch("transfers.services.payout_send",
                   return_value={"success": False, "status": "FAILED", "message": reason}):
            res = self.client.post("/api/transfers/send/", data=json.dumps({
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            }), content_type="application/json")
        self.assertFalse(res.json().get("success"))
        txn = Transaction.objects.filter(user=self.user, direction=Transaction.OUT).first()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(txn.meta.get("failure"), reason)
        self.assertEqual(txn.meta.get("failure_status"), "FAILED")
        # …and the money came back.
        self.assertEqual(get_or_create_wallet(self.user).balance, Decimal("50000.00"))

    def test_the_admin_column_reads_it_back(self):
        from django.contrib.admin.sites import AdminSite

        from wallet.admin import TransactionAdmin
        from wallet.models import Transaction as Txn

        row = Txn(meta={"failure": "Debit account is restricted"})
        self.assertEqual(TransactionAdmin(Txn, AdminSite()).failure_reason(row),
                         "Debit account is restricted")
        self.assertEqual(TransactionAdmin(Txn, AdminSite()).failure_reason(Txn(meta={})), "—")


class NoSourceAccountTests(TestCase):
    """With no NUBAN of their own and no shared pool, there is no account to debit.
    payout_send does refuse — but only after the debit, and with "Payouts are
    temporarily unavailable, please try again shortly", which reads as a passing
    outage and invites the one thing that cannot work: waiting."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000041", "nosrc@zitch.test", balance="50000")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", color="#E32119")
        # Wallet exists (make_user credits it) but was never provisioned a NUBAN.

    def send(self):
        res = self.client.post("/api/transfers/send/", data=json.dumps({
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
        }), content_type="application/json")
        return res, res.json()

    @override_settings(WEMA={"SOURCE_ACCOUNT": "", "KEYS": {}, "CHANNEL_ID": ""})
    @patch("utility.providers.payout_live", return_value=True)
    def test_says_the_account_is_not_set_up_and_never_debits(self, _live):
        before = get_or_create_wallet(self.user).balance
        with patch("transfers.services.payout_send") as send:
            _, body = self.send()
        self.assertFalse(body.get("success"))
        self.assertIn("isn't set up yet", body["message"])
        send.assert_not_called()
        self.assertEqual(get_or_create_wallet(self.user).balance, before)
        self.assertEqual(
            Transaction.objects.filter(user=self.user, direction=Transaction.OUT).count(), 0)

    @override_settings(WEMA={"SOURCE_ACCOUNT": "0100000001", "KEYS": {}, "CHANNEL_ID": ""})
    @patch("utility.providers.payout_live", return_value=True)
    def test_a_configured_pool_still_lets_the_transfer_through(self, _live):
        # The pool is the documented fallback for a sender with no NUBAN yet.
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            _, body = self.send()
        self.assertTrue(body.get("success"))


class SavedBeneficiaryTests(TestCase):
    """Keeping a recipient, naming them, and removing them.

    The distinction under test throughout: every payout records a RECENT, and
    only the customer turns a recent into a saved beneficiary. The two are kept
    apart because the send screen treats a row as proof that money once reached
    that account, and paying by name is only offered against names a customer
    chose for themselves.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="50000")
        self.other, self.other_token = make_user("08010000002", "bola@zitch.test")
        self.row = Beneficiary.objects.create(
            user=self.user, name="JOHN DOE", account_number="0123456789",
            bank_name="GTBank", bank_code="058")

    def post(self, path, payload):
        res = self.client.post(path, data=json.dumps(payload), content_type="application/json")
        return res, res.json()

    def test_a_payout_records_a_recent_not_a_saved_beneficiary(self):
        bank = Bank.objects.create(code="gtb2", name="Zenith", bank_code="057")
        from .services import execute_payout
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            execute_payout(self.user, Decimal("1000"), "0999999999", bank,
                           "MUSA ADAMU", channel="app")
        row = Beneficiary.objects.get(user=self.user, account_number="0999999999")
        self.assertFalse(row.saved)
        self.assertEqual(row.nickname, "")

    def test_saving_keeps_the_recipient(self):
        res, body = self.post("/api/transfers/beneficiaries/save/", {
            "access_token": self.token, "beneficiary_id": self.row.id})
        self.assertEqual(res.status_code, 200)
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)
        self.assertTrue(body["beneficiary"]["saved"])

    def test_saving_with_a_nickname_sets_the_display_name(self):
        _, body = self.post("/api/transfers/beneficiaries/save/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "Mum"})
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "Mum")
        self.assertEqual(body["beneficiary"]["display_name"], "Mum")
        # The holder name is untouched: bank_transfer re-confirms against it.
        self.assertEqual(self.row.name, "JOHN DOE")

    def test_naming_a_recent_also_keeps_it(self):
        # Otherwise a customer names someone "Mum" in the app and then cannot pay
        # "Mum" in the chat, because paying by name only reads saved rows.
        self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "Mum"})
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)

    def test_a_duplicate_nickname_is_refused(self):
        second = Beneficiary.objects.create(
            user=self.user, name="MUSA ADAMU", account_number="0999999999",
            bank_name="Zenith", saved=True, nickname="Mum")
        res, body = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "mum"})
        self.assertEqual(res.status_code, 409)
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "")
        self.assertEqual(second.nickname, "Mum")

    def test_a_nickname_of_digits_is_refused(self):
        # In a chat message a numeric nickname is indistinguishable from an
        # account number, and both arrive as bare text.
        res, _ = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": "0123456789"})
        self.assertEqual(res.status_code, 400)

    def test_clearing_the_nickname_leaves_the_recipient_saved(self):
        self.row.saved, self.row.nickname = True, "Mum"
        self.row.save()
        _, body = self.post("/api/transfers/beneficiaries/rename/", {
            "access_token": self.token, "beneficiary_id": self.row.id, "nickname": ""})
        self.row.refresh_from_db()
        self.assertEqual(self.row.nickname, "")
        self.assertTrue(self.row.saved)
        self.assertEqual(body["beneficiary"]["display_name"], "JOHN DOE")

    def test_deleting_removes_the_row_entirely(self):
        # Not merely unsaved: a row left behind still answers the send screen's
        # typed-account fast path, which is not what "remove" means.
        res, _ = self.post("/api/transfers/beneficiaries/delete/", {
            "access_token": self.token, "beneficiary_id": self.row.id})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Beneficiary.objects.filter(pk=self.row.pk).exists())

    def test_another_customers_recipient_is_untouchable(self):
        for path in ("save", "rename", "delete"):
            res, _ = self.post(f"/api/transfers/beneficiaries/{path}/", {
                "access_token": self.other_token, "beneficiary_id": self.row.id,
                "nickname": "Mine"})
            self.assertEqual(res.status_code, 404, path)
        self.row.refresh_from_db()
        self.assertFalse(self.row.saved)
        self.assertEqual(self.row.nickname, "")

    def test_the_list_still_carries_what_shipped_apps_read(self):
        # A phone that has not updated must keep rendering exactly what it does
        # today, so `name` and `initials` stay off the holder name.
        self.row.nickname, self.row.saved = "Mum", True
        self.row.save()
        _, body = self.post("/api/transfers/beneficiaries/", {"access_token": self.token})
        item = body["beneficiaries"][0]
        self.assertEqual(item["name"], "JOHN DOE")
        self.assertEqual(item["initials"], "JD")
        self.assertEqual(item["display_name"], "Mum")
        self.assertTrue(item["saved"])

    def test_the_list_is_not_filtered_to_saved(self):
        # The send screen's fast path reads this list; filtering would empty it.
        _, body = self.post("/api/transfers/beneficiaries/", {"access_token": self.token})
        self.assertEqual(len(body["beneficiaries"]), 1)

    def test_a_repeat_payout_never_unsaves_or_unnames(self):
        self.row.saved, self.row.nickname = True, "Mum"
        self.row.save()
        bank = Bank.objects.create(code="gtb2", name="GTBank", bank_code="058")
        from .services import execute_payout
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            execute_payout(self.user, Decimal("1000"), "0123456789", bank,
                           "JOHN DOE", channel="app")
        self.row.refresh_from_db()
        self.assertTrue(self.row.saved)
        self.assertEqual(self.row.nickname, "Mum")

    def test_a_send_names_the_row_it_just_wrote(self):
        bank = Bank.objects.create(code="gtb3", name="Access", bank_code="044")
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0777777777", "bank": "gtb3",
                "name": "MUSA ADAMU", "amount": "1000", "transaction_pin": "1234"})
        self.assertTrue(body.get("success"), body)
        row = Beneficiary.objects.get(user=self.user, account_number="0777777777")
        self.assertEqual(body["beneficiary_id"], row.id)


class PayoutFrequencyTests(TestCase):
    """A recipient earns their place in the address book.

    Every payout is remembered — that is what fills the send screen in from a
    typed account number — but being remembered is not the same as being kept.
    Keeping is the customer's own decision, or the conclusion of having paid
    somebody so many times that asking would be pedantic.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test", balance="500000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def pay(self):
        from .services import execute_payout
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "success"}):
            return execute_payout(self.user, Decimal("1000"), "0123456789", self.bank,
                                  "JOHN DOE", channel="app")

    def row(self):
        return Beneficiary.objects.get(user=self.user, account_number="0123456789")

    def test_every_payout_is_counted(self):
        for _ in range(3):
            self.pay()
        self.assertEqual(self.row().times_paid, 3)

    def test_nobody_is_offered_before_the_third_payout(self):
        self.assertFalse(self.pay().beneficiary_offer_save)
        self.assertFalse(self.pay().beneficiary_offer_save)

    def test_the_third_payout_is_worth_offering(self):
        self.pay(); self.pay()
        self.assertTrue(self.pay().beneficiary_offer_save)

    def test_a_saved_recipient_is_never_offered_again(self):
        self.pay(); self.pay(); self.pay()
        r = self.row(); r.saved = True; r.save()
        self.assertFalse(self.pay().beneficiary_offer_save)

    def test_fifty_payouts_keep_the_recipient_without_asking(self):
        Beneficiary.objects.create(
            user=self.user, name="JOHN DOE", account_number="0123456789",
            bank_name="GTBank", times_paid=AUTO_SAVE_AFTER - 1)
        self.pay()
        self.assertTrue(self.row().saved)

    def test_the_count_survives_two_payouts_settling_at_once(self):
        # Read-modify-write would lose one, and the count is what decides whether
        # somebody is offered and when they are kept outright.
        self.pay()
        Beneficiary.objects.filter(pk=self.row().pk).update(times_paid=7)
        self.pay()
        self.assertEqual(self.row().times_paid, 8)


class BankShortFormTests(TestCase):
    """What people actually call the banks.

    We store one name each, usually the short trading name, so the legal name off
    a statement matched nothing at all — which reads as the bank not existing
    rather than as us knowing it by another name.
    """

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010000001", "ada@zitch.test")
        Bank.objects.create(code="gtb", name="GTBank", bank_code="058", active=True)
        Bank.objects.create(code="uba", name="UBA", bank_code="033", active=True)
        Bank.objects.create(code="scb", name="Standard Chartered", bank_code="068", active=True)

    def test_the_legal_name_finds_the_bank(self):
        from whatsapp.router import _match_banks
        for phrase in ("guaranty trust bank", "guaranty trust", "gtco", "gt"):
            hits = _match_banks(phrase)
            self.assertEqual([b.code for b in hits], ["gtb"], phrase)

    def test_an_initialism_finds_the_bank(self):
        from whatsapp.router import _match_banks
        self.assertEqual([b.code for b in _match_banks("scb")], ["scb"])
        self.assertEqual([b.code for b in _match_banks("united bank for africa")], ["uba"])

    def test_an_unknown_phrase_still_matches_nothing(self):
        from whatsapp.router import _match_banks
        self.assertEqual(_match_banks("not a bank at all"), [])

    def test_no_alias_is_claimed_by_two_banks(self):
        # An alias is matched exactly and wins outright, so one claimed twice
        # would silently route money to whichever sorted first.
        from .bank_aliases import BANK_ALIASES
        seen = {}
        for slug, (_short, names) in BANK_ALIASES.items():
            for n in names:
                self.assertNotIn(n, seen, f"{n!r} claimed by {seen.get(n)} and {slug}")
                seen[n] = slug

    def test_the_app_is_told_the_short_form(self):
        res = self.client.post("/api/transfers/banks/", data=json.dumps({}),
                               content_type="application/json")
        rows = {b["code"]: b for b in res.json()["banks"]}
        self.assertEqual(rows["gtb"]["short"], "GT")
        self.assertIn("guaranty trust bank", rows["gtb"]["aliases"])
        # Additive: everything a shipped build reads is still there.
        self.assertEqual(rows["gtb"]["name"], "GTBank")
