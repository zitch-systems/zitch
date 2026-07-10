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

from .models import Bank, Beneficiary


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
        # Without a live Monnify name-enquiry rail the detection is a placeholder, so
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

    def test_pending_payout_settled_by_webhook(self):
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "processing"}):
            _, body = self.post("/api/transfers/send/", {
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            })
        ref = body["reference"]
        event = {"eventType": "SUCCESSFUL_DISBURSEMENT", "eventData": {"reference": ref}}
        self.client.post("/api/transfers/webhook/", data=json.dumps(event),
                         content_type="application/json", HTTP_MONNIFY_SIGNATURE="mock")
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.SUCCESS)

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

    def test_disbursement_webhook_refunds_failed_payout(self):
        """A payout that succeeds on send but fails later (webhook) is refunded."""
        _, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
        })
        ref = body["reference"]
        self.assertEqual(self.balance(), Decimal("40000"))  # debited on send

        event = {"eventType": "FAILED_DISBURSEMENT", "eventData": {"reference": ref, "status": "failed"}}
        r = self.client.post("/api/transfers/webhook/", data=json.dumps(event),
                             content_type="application/json", HTTP_MONNIFY_SIGNATURE="mock")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.balance(), Decimal("50000"))  # refunded by the webhook
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.FAILED)

        # Duplicate webhook (Monnify retry) must not double-refund.
        self.client.post("/api/transfers/webhook/", data=json.dumps(event),
                         content_type="application/json", HTTP_MONNIFY_SIGNATURE="mock")
        self.assertEqual(self.balance(), Decimal("50000"))

    def test_disbursement_webhook_ignores_success_event(self):
        """A successful-disbursement callback is a no-op (already settled)."""
        _, body = self.post("/api/transfers/send/", {
            "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
            "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
        })
        event = {"eventType": "SUCCESSFUL_DISBURSEMENT", "eventData": {"reference": body["reference"]}}
        self.client.post("/api/transfers/webhook/", data=json.dumps(event),
                         content_type="application/json", HTTP_MONNIFY_SIGNATURE="mock")
        self.assertEqual(self.balance(), Decimal("40000"))  # unchanged
        self.assertEqual(Transaction.objects.get(reference=body["reference"]).transaction_status, Transaction.SUCCESS)

    def test_ambiguous_send_timeout_holds_debit_never_refunds(self):
        """CRITICAL: a network timeout on the non-idempotent disburse POST is
        AMBIGUOUS — Monnify may already have paid the recipient. The debit MUST be
        HELD (PENDING + reconcile), never refunded. Refunding here drains the float
        (recipient keeps the money, sender made whole) and a retry double-pays. The
        reconcile flag then lets reconcile_payouts settle/reverse it on the true
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
        """CRITICAL: a payout Monnify ACCEPTED but reported with a status this code
        doesn't recognise must be treated exactly like PENDING — debit held,
        response pending — not as a failure. Misclassifying it refunded an
        in-flight transfer (double-spend) and showed the user "Error / success"."""
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "pending", "reference": "X"}):
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


class MonnifyPayoutReconcileTests(TestCase):
    """The reconcile_payouts poller settles/reverses PENDING payouts when a Monnify
    disbursement webhook is missed — the safety net behind the webhook."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08010009001", "recon@zitch.test", balance="50000")
        self.bank = Bank.objects.create(code="gtb", name="GTBank", bank_code="058")

    def balance(self):
        return get_or_create_wallet(self.user).balance

    def _pending_payout(self):
        # A payout the rail ACCEPTED but left PENDING (awaiting the webhook): the
        # wallet stays debited and the row is flagged for reconciliation.
        with patch("transfers.services.payout_send",
                   return_value={"success": True, "status": "pending"}):
            res = self.client.post("/api/transfers/send/", data=json.dumps({
                "access_token": self.token, "account_number": "0123456789", "bank": "gtb",
                "name": "John Doe", "amount": "10000", "transaction_pin": "1234",
            }), content_type="application/json")
        ref = res.json()["reference"]
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.PENDING)
        self.assertEqual(self.balance(), Decimal("40000"))  # debited on send
        return ref

    def _run(self):
        call_command("reconcile_payouts", "--older-than-minutes", "0", stdout=StringIO())

    def test_settles_pending_payout_when_status_success(self):
        ref = self._pending_payout()
        with patch("utility.monnify.verify_payout", return_value={"success": True, "status": "success"}):
            self._run()
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.SUCCESS)
        self.assertEqual(self.balance(), Decimal("40000"))  # settled, not refunded

    def test_reverses_pending_payout_when_status_failed(self):
        ref = self._pending_payout()
        with patch("utility.monnify.verify_payout", return_value={"success": False, "status": "failed"}):
            self._run()
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("50000"))  # refunded

    def test_leaves_still_pending_payout_untouched(self):
        ref = self._pending_payout()
        with patch("utility.monnify.verify_payout",
                   return_value={"success": False, "pending": True, "status": "pending"}):
            self._run()
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.PENDING)
        self.assertEqual(self.balance(), Decimal("40000"))  # untouched

    def test_unreachable_status_leaves_pending(self):
        # A query that can't reach Monnify must NOT reverse money — leave PENDING.
        ref = self._pending_payout()
        with patch("utility.monnify.verify_payout",
                   return_value={"success": False, "message": "unreachable"}):
            self._run()
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.PENDING)
        self.assertEqual(self.balance(), Decimal("40000"))

    @override_settings(PAYOUT_PROVIDER="wema")
    def test_does_not_query_when_wema_is_payout_rail(self):
        ref = self._pending_payout()
        with patch("utility.monnify.verify_payout") as mv:
            self._run()
        mv.assert_not_called()
        self.assertEqual(Transaction.objects.get(reference=ref).transaction_status, Transaction.PENDING)
