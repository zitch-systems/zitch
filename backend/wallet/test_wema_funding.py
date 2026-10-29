"""Tests for Wema/ALAT wallet funding — the OTP provisioning endpoints and the
credit-reconciliation poller (ALAT has no inbound-credit webhook).

- Provisioning: create -> verify-otp mints + persists a NUBAN with a WEMA
  account_reference (mock flow, PAYMENT_PROVIDER=wema).
- Reconcile: reconcile_wema credits an inbound (creditType=='Credit') deposit
  once, is idempotent across re-polls, and ignores debits / zero rows.
"""
import json
import importlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import hash_identifier
from wallet.models import (
    ReversalEvidence,
    ReversalEvidenceObservation,
    ReversalEvidenceResolution,
    Transaction,
    Wallet,
    WemaFaceSession,
    WemaProvisioningAttempt,
)
from wallet.services import apply_wema_credit, wema_account_reference
from wallet.tests import make_transaction_at, make_user


def _tx(ref, amount, credit=True, **extra):
    row = {"referenceId": ref, "amount": amount,
           "date": timezone.now().isoformat(),
           "creditType": "Credit" if credit else "Debit",
           "status": extra.get("status", "Successfull"),
           "narration": extra.get("narration", "Transfer in"),
           "sender": extra.get("sender", "GTBANK / JOHN")}
    # Production funding is fail-closed: only Wema's documented (and misspelled)
    # terminal status is settled. Tests pass status explicitly for Pending/Failed/
    # blank guards; ordinary fixtures model a settled provider row by default.
    return row








class WemaReconcileTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08030000999", "r@zitch.app")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0155500011"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_reference", "bank_name"])

    def _run(self, txns):
        # Exercise reviewed historical rows directly. The retired transport is
        # never restored by the current reconciliation scheduler.
        for row in txns:
            apply_wema_credit(self.wallet, row)


    def test_credits_inbound_deposit_once(self):
        self._run([_tx("WEMA-DEP-1", 2500)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("2500.00"))
        # Ledger key is namespaced (WEMA-CR-) so it can't collide with ZTRF/ZPAY/ZFND.
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-WEMA-DEP-1",
                                                   direction=Transaction.IN).exists())
        # Re-poll the same window: idempotent on referenceId, no double-credit.
        self._run([_tx("WEMA-DEP-1", 2500)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("2500.00"))

    def test_reference_namespace_no_collision_with_payout(self):
        # A prior payout burned ledger reference "ZTRF-CLASH". A Wema inbound whose
        # referenceId is the same string must STILL credit (namespaced), not be
        # silently dropped as a duplicate.
        from wallet.services import make_reference  # noqa: F401
        Transaction.objects.create(user=self.user, service="Transfer out", amount=Decimal("10"),
                                   direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
                                   reference="ZTRF-CLASH")
        self._run([_tx("ZTRF-CLASH", 900)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("900.00"))
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-ZTRF-CLASH").exists())

    def test_credits_comma_formatted_amount(self):
        self._run([_tx("WEMA-DEP-C", "12,500.75")])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("12500.75"))

    def test_ignores_debits_and_zero_rows(self):
        self._run([_tx("WEMA-OUT-1", 1000, credit=False), _tx("WEMA-ZERO-1", 0)])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))

    def test_apply_wema_credit_skips_non_credit(self):
        self.assertIsNone(apply_wema_credit(self.wallet, _tx("X", 500, credit=False)))
        self.assertIsNone(apply_wema_credit(self.wallet, {"referenceId": "", "amount": 500,
                                                          "creditType": "Credit"}))
        # Unparseable amount on a real credit row -> skipped (logged), not credited.
        self.assertIsNone(apply_wema_credit(self.wallet, {"referenceId": "Y", "amount": "N/A",
                                                          "creditType": "Credit"}))

    def test_only_explicit_success_credit_row_is_funded(self):
        # ALAT TransactionStatus {Default, Successfull, Failed, Pending}. A settled
        # credit funds the wallet; every other/absent value stays uncredited so a
        # deposit never creates spendable money before the bank proves settlement.
        self.assertIsNotNone(apply_wema_credit(self.wallet,
            {"referenceId": "S1", "amount": 700, "creditType": "Credit", "status": "Successfull"}))
        for index, status in enumerate(("Pending", "Failed", "Default", "Unexpected", "", None)):
            row = {"referenceId": f"U{index}", "amount": 700,
                   "creditType": "Credit"}
            if status is not None:
                row["status"] = status
            self.assertIsNone(apply_wema_credit(self.wallet, row), repr(status))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("700.00"))

    def test_pending_credit_funds_once_it_settles(self):
        # A Pending row is skipped, then the SAME referenceId re-appears settled on a
        # later sweep and funds exactly once — no permanent drop, no double-credit.
        self._run([_tx("WEMA-LATE", 1500, status="Pending")])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))
        self._run([_tx("WEMA-LATE", 1500, status="Successfull")])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1500.00"))
        self._run([_tx("WEMA-LATE", 1500, status="Successfull")])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1500.00"))

    def test_posted_default_credit_from_bank_history_funds_once(self):
        row = {"referenceId": "BANK-POSTED-1", "tranId": "S42026066",
               "date": timezone.now().isoformat(),
               "rrn": " BANK-POSTED-1", "amount": 500, "balance": "500.00",
               "creditType": "Credit", "status": "Default"}
        self._run([row])
        self._run([row])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("500.00"))
        self.assertEqual(Transaction.objects.filter(reference="WEMA-CR-BANK-POSTED-1").count(), 1)

    def test_reversed_credit_row_not_funded(self):
        # Defense-in-depth beyond the documented enum: the explicit allowlist also
        # keeps a re-spelled non-final status (e.g. Reversed) unsettled.
        self.assertIsNone(apply_wema_credit(self.wallet,
            {"referenceId": "R1", "amount": 900, "creditType": "Credit", "status": "Reversed"}))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0.00"))


class WemaReversalGuardTests(TestCase):
    """A bounced payout landing back in the sender's OWN NUBAN as an inbound
    credit must NOT be counted twice (funding sweep + payout reversal): the sweep
    routes it through reverse_transfer (refunds at most once, ever) instead of
    crediting it as funding. docs/wema-migration.md open item 5."""

    PAYOUT_REF = "ZTCHDEADBEEF0001"

    def setUp(self):
        # Wallet already debited ₦1,000 by the payout: ₦5,000 - ₦1,000.
        self.user, _ = make_user("08030000555", "rev@zitch.app", balance="4000")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0155500055"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.save(update_fields=["account_number", "account_reference"])
        self.payout = Transaction.objects.create(
            user=self.user, service="Transfer to ADA", amount=Decimal("1000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference=self.PAYOUT_REF, meta={"reconcile": True, "bank": "GTBank"})

    def _run(self, txns, status="FAILED"):
        for row in txns:
            apply_wema_credit(self.wallet, row, [self.PAYOUT_REF])

    def _bounce(self):
        # The reversal row carries our reference in the narration (field-agnostic
        # matching, so referenceId carrying it works too — see test below).
        return _tx("ALAT-REV-77", 1000, narration=f"REV/{self.PAYOUT_REF}/bounced")

    def test_bounced_payout_refunded_exactly_once(self):
        self._run([self._bounce()])
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        # One refund (back to ₦5,000) — NOT the reversal + a funding credit (₦6,000).
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        evidence = Transaction.objects.get(reference="WEMA-CR-ALAT-REV-77")
        self.assertEqual(evidence.transaction_status, Transaction.FAILED)
        self.assertEqual((evidence.meta or {})["wema_reversal_evidence"]
                         ["payout_reference"], self.PAYOUT_REF)
        # Re-poll of the same window: the row matches again, but reverse_transfer
        # is a no-op on the already-FAILED payout.
        self._run([self._bounce()])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))

    def test_posted_default_reversal_refunds_without_funding_credit(self):
        row = {**self._bounce(), "status": "Default", "tranId": "S42026067",
               "rrn": " ALAT-REV-77", "balance": "5000.00"}
        self._run([row])
        self._run([row])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertEqual(Transaction.objects.get(
            reference="WEMA-CR-ALAT-REV-77").transaction_status, Transaction.FAILED)

    def test_already_reversed_payout_not_credited_again(self):
        # Phase 2 (payout poller) reversed it in an earlier run…
        from wallet.services import reverse_transfer
        reverse_transfer(self.PAYOUT_REF)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        # …then the bounce appears in the polled history: no second credit.
        self._run([self._bounce()])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertEqual(Transaction.objects.get(
            reference="WEMA-CR-ALAT-REV-77").transaction_status, Transaction.FAILED)

    def test_reversal_matched_in_any_field(self):
        # Some rails echo the reference in referenceId rather than narration.
        row = _tx(self.PAYOUT_REF, 1000, narration="TRF REVERSAL")
        self._run([row])
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertEqual(Transaction.objects.get(
            reference=f"WEMA-CR-{self.PAYOUT_REF}").transaction_status,
            Transaction.FAILED)

    def test_unmatched_reversal_marker_is_quarantined(self):
        # Banks do not always echo our payout reference on a returned transfer.
        # With an outbound payout on this wallet, an explicit reversal marker is
        # held for manual reconciliation instead of being credited as funding.
        row = _tx("ALAT-REV-UNKNOWN", 1000,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")
        self.assertIsNone(apply_wema_credit(self.wallet, row, [self.PAYOUT_REF]))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4000.00"))
        claim = Transaction.objects.get(reference="WEMA-CR-ALAT-REV-UNKNOWN")
        self.assertEqual(claim.transaction_status, Transaction.FAILED)
        self.assertTrue((claim.meta or {}).get("internal_evidence"))
        self.assertTrue(ReversalEvidence.objects.filter(
            provider_reference="ALAT-REV-UNKNOWN",
            state=ReversalEvidence.ACTIVE,
        ).exists())

    def test_reversal_marker_is_quarantined_even_when_recent_refs_are_empty(self):
        # An empty recent set is not proof this is funding: the originating payout
        # can be old or can use metadata written by a legacy client.  Fail closed
        # on the provider's explicit reversal marker and page for reconciliation.
        user, _ = make_user("08030000558", "marker-only@zitch.app", balance="700")
        wallet = Wallet.objects.get(user=user)
        row = _tx("ALAT-REV-NO-REF", 300,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")
        with patch("utility.alerts.alert") as alerted:
            self.assertIsNone(apply_wema_credit(wallet, row, self_refs=[]))
        alerted.assert_called_once()
        self.assertIn("unmatched_reversal", alerted.call_args[0][0])
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("700.00"))
        self.assertTrue(Transaction.objects.filter(
            reference="WEMA-CR-ALAT-REV-NO-REF",
            transaction_status=Transaction.FAILED,
            meta__internal_evidence=True,
        ).exists())

    def test_unmarked_funding_still_credits_when_recent_refs_are_empty(self):
        # The fail-closed rule is marker-specific.  An ordinary settled inbound
        # row still funds the wallet, including a wallet with no recent payouts.
        user, _ = make_user("08030000559", "ordinary-funding@zitch.app", balance="0")
        wallet = Wallet.objects.get(user=user)
        credited = apply_wema_credit(
            wallet, _tx("WEMA-ORDINARY-DEP", 450, narration="Transfer in"), self_refs=[])
        self.assertIsNotNone(credited)
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("450.00"))
        self.assertTrue(Transaction.objects.filter(
            reference="WEMA-CR-WEMA-ORDINARY-DEP").exists())

    def test_reversal_named_field_with_false_value_does_not_quarantine_funding(self):
        user, _ = make_user("08030000560", "false-marker@zitch.app", balance="0")
        wallet = Wallet.objects.get(user=user)
        row = _tx("WEMA-FALSE-MARKER", 225, narration="Transfer in")
        row["reversal"] = False
        row["nested"] = {"isReversal": False}

        credited = apply_wema_credit(wallet, row, self_refs=[])

        self.assertIsNotNone(credited)
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("225.00"))

    def test_truthy_boolean_reversal_marker_is_quarantined(self):
        user, _ = make_user("08030000561", "true-marker@zitch.app", balance="0")
        wallet = Wallet.objects.get(user=user)
        row = _tx("WEMA-TRUE-MARKER", 225, narration="Transfer in")
        row["nested"] = {"isReversal": True}

        with patch("utility.alerts.alert") as alerted:
            credited = apply_wema_credit(wallet, row, self_refs=[])

        self.assertIsNone(credited)
        alerted.assert_called_once()
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("0.00"))
        self.assertTrue(Transaction.objects.filter(
            reference="WEMA-CR-WEMA-TRUE-MARKER",
            transaction_status=Transaction.FAILED,
            meta__internal_evidence=True,
        ).exists())

    def test_truthy_string_reversal_marker_is_quarantined(self):
        user, _ = make_user("08030000562", "string-marker@zitch.app", balance="0")
        wallet = Wallet.objects.get(user=user)
        row = _tx("WEMA-STRING-MARKER", 225, narration="Transfer in")
        row["is_reversal"] = "yes"

        with patch("utility.alerts.alert"):
            credited = apply_wema_credit(wallet, row, self_refs=[])

        self.assertIsNone(credited)
        self.assertEqual(Wallet.objects.get(user=user).balance, Decimal("0.00"))

    def test_a_quarantined_reversal_pages_rather_than_waiting_for_a_grep(self):
        # A quarantined row credits nothing and self-heals on no later sweep — the
        # only way anyone learns the customer's money is stuck is by reading it off
        # this alert. Silence here is silence on a real customer's money.
        row = _tx("ALAT-REV-UNKNOWN", 1000,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")
        with patch("utility.alerts.alert") as alerted:
            apply_wema_credit(self.wallet, row, [self.PAYOUT_REF])
        alerted.assert_called_once()
        self.assertIn("unmatched_reversal", alerted.call_args[0][0])
        self.assertEqual(alerted.call_args[1]["reference"], "ALAT-REV-UNKNOWN")

    def test_unmatched_reversal_is_one_durable_case_across_repolls(self):
        row = _tx("ALAT-REV-DURABLE", 375,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")

        with patch("utility.alerts.alert") as alerted:
            apply_wema_credit(self.wallet, row, self_refs=[])
            apply_wema_credit(self.wallet, row, self_refs=[])

        case = ReversalEvidence.objects.get(provider_reference="ALAT-REV-DURABLE")
        self.assertIsNone(case.payout_id)
        self.assertEqual(case.state, ReversalEvidence.ACTIVE)
        self.assertEqual(case.amount, Decimal("375.00"))
        self.assertEqual(case.observations.get().sightings, 2)
        self.assertEqual(Transaction.objects.filter(
            reference="WEMA-CR-ALAT-REV-DURABLE").count(), 1)
        self.assertEqual(alerted.call_count, 1)

    def test_historical_credit_does_not_hide_an_unmatched_reversal_case(self):
        from wallet.services import settle_reserved_funding

        settle_reserved_funding(
            "WEMA-CR-ALAT-HISTORIC-REV", Decimal("225.00"), self.user)
        before = Wallet.objects.get(user=self.user).balance
        row = _tx("ALAT-HISTORIC-REV", 225,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")

        with patch("utility.alerts.alert"):
            self.assertIsNone(apply_wema_credit(self.wallet, row, self_refs=[]))

        case = ReversalEvidence.objects.get(provider_reference="ALAT-HISTORIC-REV")
        self.assertEqual(case.reason, "already_credited_unmatched")
        self.assertEqual(case.ledger_transaction.transaction_status,
                         Transaction.SUCCESS)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)

    def test_third_party_deposit_still_credits_alongside_payouts(self):
        # A genuine deposit (no payout reference anywhere) credits normally even
        # while the user has an outstanding payout.
        self._run([_tx("WEMA-DEP-9", 2500)], status="IN_PROGRESS")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("6500.00"))
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-WEMA-DEP-9").exists())

    def test_other_users_payout_arriving_here_still_credits(self):
        # Recipient-side: an inbound credit carrying ANOTHER user's payout
        # reference is that payout ARRIVING, not a reversal — it must credit.
        other, _ = make_user("08030000556", "other@zitch.app", balance="0")
        Transaction.objects.create(
            user=other, service="Transfer to REV", amount=Decimal("800"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="ZTCHCAFEBABE0002", meta={"reconcile": True, "bank": "Wema Bank"})
        self._run([_tx("ALAT-IN-88", 800, narration="TRF/ZTCHCAFEBABE0002/from other")],
                  status="IN_PROGRESS")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4800.00"))
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-ALAT-IN-88").exists())








@override_settings(PAYMENT_PROVIDER="wema")
class ReconnectBankAccountAdminTests(TestCase):
    """The operator's route for a wallet left with no account number.

    Every payout from such a wallet is refused for having no source to debit, so this
    action existed to attach the NUBAN the rail already held. It no longer reaches the
    rail and only reports that recovery is waiting on Wema's wallet-details callback —
    see the open question on AdoptExistingWemaAccountTests."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from wallet.admin import WalletAdmin

        self.user, _ = make_user("08030000777", "reconnect@zitch.app")
        self.admin = WalletAdmin(Wallet, AdminSite())
        self.messages = []
        self.admin.message_user = lambda req, msg, level=None: self.messages.append(msg)

    def _run(self):
        wallet = Wallet.objects.get(user=self.user)
        self.admin.reconnect_bank_account(None, Wallet.objects.filter(pk=wallet.pk))
        wallet.refresh_from_db()
        return wallet

    def test_the_operator_action_no_longer_reaches_the_rail(self):
        """The admin action can no longer attach anything; it reports and stops.

        This used to be the operator's way out for a wallet with no account number —
        every payout from such a wallet is refused for having no source to debit, so
        the capability mattered. It now returns the pending-callback message whatever
        the rail holds.
        """
        found = {"success": True, "account_number": "0123456789",
                 "account_name": "ADA EZE", "bank_name": "Wema Bank"}
        with patch("utility.wema.get_account_details", return_value=found) as fetch, \
             patch("utility.wema.lift_debit_restriction") as pnd:
            wallet = self._run()
        self.assertEqual(wallet.account_number, "")
        fetch.assert_not_called()
        pnd.assert_not_called()
        self.assertIn("no funding account is available", " ".join(self.messages).lower())

    def test_no_wallet_product_is_consulted_at_all(self):
        """Neither the BVN nor the NIN wallet product is asked any more.

        This test previously pinned the search ORDER across both products and every
        phone normalisation the bank might hold the customer under. That whole search
        is gone, so the surviving assertion is the deliberate absence: nothing reaches
        the rail. Kept rather than deleted, because a silent return of the phone
        lookup should fail a test, not slip back in unnoticed.
        """
        calls = []

        def by_product(phone, *, bvn=False):
            calls.append(bvn)
            return {"success": True, "account_number": "0123456789",
                    "account_name": "ADA EZE", "bank_name": "Wema Bank"}

        with patch("utility.wema.get_account_details", side_effect=by_product), \
             patch("utility.wema.lift_debit_restriction", return_value={"success": True}):
            wallet = self._run()
        self.assertEqual(calls, [])
        self.assertEqual(wallet.account_number, "")

    def test_never_replaces_an_account_it_already_has(self):
        wallet = Wallet.objects.get(user=self.user)
        wallet.account_number = "0999999999"
        wallet.save(update_fields=["account_number"])
        with patch("utility.wema.get_account_details") as fetch:
            wallet = self._run()
        fetch.assert_not_called()
        self.assertEqual(wallet.account_number, "0999999999")
        self.assertIn("already has an account number", " ".join(self.messages))

    def test_reports_when_the_rail_holds_nothing(self):
        with patch("utility.wema.get_account_details",
                   return_value={"success": False, "message": "no record"}):
            wallet = self._run()
        self.assertEqual(wallet.account_number, "")
        # The operator is told what it is waiting on, not what the rail said —
        # nothing asks the rail any more.
        self.assertIn("no funding account is available", " ".join(self.messages).lower())


@override_settings(PAYOUT_PROVIDER="wema")
class WemaPartialReversalTests(TestCase):
    """A credit quoting a payout reference but NOT its amount is not a reversal.

    reverse_transfer refunds the payout's amount, not the amount that landed, so
    treating a partial return as a clean reversal credits the wrong number and
    drops the real money at the same time.
    """

    PAYOUT_REF = "ZTCHDEADBEEF0002"

    def setUp(self):
        # Wallet already debited N1,000 by the payout: N5,000 - N1,000.
        self.user, self.token = make_user(
            "08030000556", "partial@zitch.app", balance="4000")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0155500056"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.save(update_fields=["account_number", "account_reference"])
        self.payout = Transaction.objects.create(
            user=self.user, service="Transfer to ADA", amount=Decimal("1000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference=self.PAYOUT_REF, meta={"reconcile": True, "bank": "GTBank"})

    def _row(self, amount):
        return _tx("ALAT-REV-PART", amount, narration=f"REFUND {self.PAYOUT_REF}")

    def test_a_partial_return_is_quarantined_not_refunded_in_full(self):
        with patch("utility.alerts.alert") as alerted:
            self.assertIsNone(apply_wema_credit(self.wallet, self._row(500),
                                                [self.PAYOUT_REF]))
        # Neither refunded at the payout's value nor credited as funding.
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4000.00"))
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.PENDING)
        evidence = Transaction.objects.get(reference="WEMA-CR-ALAT-REV-PART")
        self.assertEqual(evidence.transaction_status, Transaction.FAILED)
        quarantine = (self.payout.meta or {}).get("wema_reversal_quarantine") or {}
        self.assertTrue(quarantine.get("active"))
        self.assertEqual(quarantine.get("reason"), "partial_amount")
        # And it pages, because the money is now stuck pending a human.
        self.assertTrue([c for c in alerted.call_args_list
                         if "reversal_quarantined" in str(c)], "a partial return must page")

        # The evidence is retained for operations/idempotency but is not a fake
        # failed credit in the customer's history or detail API.
        history = self.client.post(
            "/api/user-transaction-history/",
            data={"access_token": self.token},
            content_type="application/json",
        ).json()["all_site_transactions"]
        self.assertNotIn("WEMA-CR-ALAT-REV-PART",
                         {row["reference"] for row in history})
        detail = self.client.post(
            "/api/transaction/status/",
            data={"access_token": self.token,
                  "reference": "WEMA-CR-ALAT-REV-PART"},
            content_type="application/json",
        )
        self.assertEqual(detail.status_code, 404)

    def test_migration_backfills_legacy_json_hold_and_bounds_metadata(self):
        marker = {
            "active": True,
            "reason": "partial_amount",
            "evidence": [{
                "reason": "partial_amount",
                "inbound_reference": "ALAT-LEGACY-BACKFILL",
                "ledger_reference": "WEMA-CR-ALAT-LEGACY-BACKFILL",
                "received_amount": "400.00",
                "resolved": False,
            }],
            "inbound_references": [f"legacy-{index}" for index in range(50)],
            "resolution_history": [{"index": index} for index in range(50)],
        }
        self.payout.meta = {**(self.payout.meta or {}),
                            "wema_reversal_quarantine": marker}
        self.payout.save(update_fields=["meta"])
        evidence_row = Transaction.objects.create(
            user=self.user,
            service="Payout reversal evidence",
            amount=Decimal("400.00"),
            direction=Transaction.IN,
            transaction_status=Transaction.FAILED,
            reference="WEMA-CR-ALAT-LEGACY-BACKFILL",
            meta={"internal_evidence": True},
        )

        migration = importlib.import_module(
            "wallet.migrations.0021_reversal_evidence")
        from django.apps import apps

        migration.backfill_reversal_evidence(apps, None)

        case = ReversalEvidence.objects.get(
            provider_reference="ALAT-LEGACY-BACKFILL")
        self.assertEqual(case.payout, self.payout)
        self.assertEqual(case.ledger_transaction, evidence_row)
        self.assertEqual(case.initial_reason, "partial_amount")
        self.assertEqual(case.reason, "partial_amount")
        self.payout.refresh_from_db()
        summary = self.payout.meta["wema_reversal_quarantine"]
        self.assertTrue(summary["active"])
        self.assertNotIn("evidence", summary)
        self.assertNotIn("inbound_references", summary)
        self.assertNotIn("resolution_history", summary)

    def test_migration_keeps_same_reference_associated_with_both_payouts(self):
        second = Transaction.objects.create(
            user=self.user, service="Transfer to BOB", amount=Decimal("1000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="ZTCHDEADBEEF0003", meta={"bank": "GTBank"},
        )
        for payout in (self.payout, second):
            payout.meta = {
                **(payout.meta or {}),
                "wema_reversal_quarantine": {
                    "active": True,
                    "evidence": [{
                        "reason": "partial_amount",
                        "inbound_reference": "ALAT-LEGACY-SHARED",
                        "ledger_reference": "WEMA-CR-ALAT-LEGACY-SHARED",
                        "received_amount": "500.00",
                    }],
                },
            }
            payout.save(update_fields=["meta"])
        Transaction.objects.create(
            user=self.user, service="Payout reversal evidence",
            amount=Decimal("500"), direction=Transaction.IN,
            transaction_status=Transaction.FAILED,
            reference="WEMA-CR-ALAT-LEGACY-SHARED",
            meta={"internal_evidence": True},
        )
        migration = importlib.import_module("wallet.migrations.0021_reversal_evidence")
        from django.apps import apps

        migration.backfill_reversal_evidence(apps, None)

        case = ReversalEvidence.objects.get(
            provider_reference="ALAT-LEGACY-SHARED")
        self.assertEqual(case.state, ReversalEvidence.CONFLICT)
        self.assertEqual(case.reason, "provider_reference_reused")
        self.assertEqual(
            set(case.associated_payouts.values_list("pk", flat=True)),
            {self.payout.pk, second.pk},
        )
        for payout in (self.payout, second):
            payout.refresh_from_db()
            self.assertTrue(payout.meta["wema_reversal_quarantine"]["active"])

    def test_migration_quarantines_a_reused_legacy_ledger_row_without_crashing(self):
        ledger = Transaction.objects.create(
            user=self.user, service="Payout reversal evidence",
            amount=Decimal("400"), direction=Transaction.IN,
            transaction_status=Transaction.FAILED,
            reference="WEMA-CR-LEGACY-SHARED-LEDGER",
            meta={"internal_evidence": True},
        )
        self.payout.meta = {
            **(self.payout.meta or {}),
            "wema_reversal_quarantine": {
                "active": True,
                "evidence": [{
                    "reason": "partial_amount",
                    "inbound_reference": f"ALAT-LEGACY-COLLISION-{index}",
                    "ledger_reference": ledger.reference,
                    "received_amount": "400.00",
                } for index in range(2)],
            },
        }
        self.payout.save(update_fields=["meta"])
        migration = importlib.import_module("wallet.migrations.0021_reversal_evidence")
        from django.apps import apps

        migration.backfill_reversal_evidence(apps, None)

        cases = list(ReversalEvidence.objects.filter(
            provider_reference__startswith="ALAT-LEGACY-COLLISION-"))
        self.assertEqual(len(cases), 2)
        self.assertEqual({case.state for case in cases}, {ReversalEvidence.CONFLICT})
        self.assertEqual({case.reason for case in cases}, {"ledger_reference_reused"})
        self.assertEqual(sum(case.ledger_transaction_id == ledger.pk for case in cases), 1)

    def test_migration_bounds_invalid_money_and_approval_inputs(self):
        self.payout.meta = {
            **(self.payout.meta or {}),
            "wema_reversal_quarantine": {
                "active": True,
                "evidence": [
                    {
                        "reason": "legacy_quarantine",
                        "inbound_reference": "ALAT-LEGACY-TOO-LARGE",
                        "received_amount": "1e100",
                    },
                    {
                        "reason": "legacy_resolved",
                        "inbound_reference": "ALAT-LEGACY-HUGE-APPROVAL",
                        "received_amount": "100.00",
                        "resolved": True,
                        "resolution": {
                            "disposition": "legacy_resolved",
                            "approval_id": str(2 ** 63),
                        },
                    },
                    {
                        "reason": "legacy_resolved",
                        "inbound_reference": "ALAT-LEGACY-BOOL-APPROVAL",
                        "received_amount": "100.00",
                        "resolved": True,
                        "resolution": {
                            "disposition": "legacy_resolved",
                            "approval_id": True,
                        },
                    },
                    {
                        "reason": "legacy_resolved",
                        "inbound_reference": "ALAT-LEGACY-FLOAT-APPROVAL",
                        "received_amount": "100.00",
                        "resolved": True,
                        "resolution": {
                            "disposition": "legacy_resolved",
                            "approval_id": 1.2,
                        },
                    },
                ],
            },
        }
        self.payout.save(update_fields=["meta"])
        migration = importlib.import_module("wallet.migrations.0021_reversal_evidence")
        from django.apps import apps

        migration.backfill_reversal_evidence(apps, None)

        self.assertFalse(ReversalEvidence.objects.filter(
            provider_reference="ALAT-LEGACY-TOO-LARGE").exists())
        for provider_reference in (
                "ALAT-LEGACY-HUGE-APPROVAL",
                "ALAT-LEGACY-BOOL-APPROVAL",
                "ALAT-LEGACY-FLOAT-APPROVAL"):
            with self.subTest(provider_reference=provider_reference):
                resolution = ReversalEvidenceResolution.objects.get(
                    evidence__provider_reference=provider_reference)
                self.assertIsNone(resolution.approval_id)
        self.payout.refresh_from_db()
        summary = self.payout.meta["wema_reversal_quarantine"]
        self.assertTrue(summary["active"])
        self.assertEqual(summary["reason"], "invalid_legacy_evidence")
        self.assertEqual(summary["invalid_evidence_count"], 1)
        self.assertFalse(migration.Migration.operations[-1].reversible)

    def test_repeat_partial_return_preserves_the_original_quarantine_reason(self):
        with patch("utility.alerts.alert") as alerted:
            self.assertIsNone(apply_wema_credit(
                self.wallet, self._row(500), [self.PAYOUT_REF]))
            self.assertIsNone(apply_wema_credit(
                self.wallet, self._row(500), [self.PAYOUT_REF]))

        self.payout.refresh_from_db()
        quarantine = (self.payout.meta or {}).get("wema_reversal_quarantine") or {}
        self.assertEqual(quarantine.get("reason"), "partial_amount")
        self.assertNotIn("orphaned_evidence", quarantine.get("reasons") or [])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4000.00"))
        row_alerts = [c for c in alerted.call_args_list
                      if "reversal_quarantined" in str(c)]
        self.assertEqual(len(row_alerts), 1)

    def test_terminal_status_quarantine_stays_in_the_recurring_review_alert(self):
        self.payout.transaction_status = Transaction.SUCCESS
        self.payout.meta = {**self.payout.meta, "wema_reversal_quarantine": {
            "active": True, "reason": "already_credited"}}
        self.payout.save(update_fields=["transaction_status", "meta"])
        from io import StringIO
        import json
        with patch("utility.alerts.alert") as alerted, \
             patch("utility.reconciliation.alert_due", return_value=True):
            out = StringIO()
            call_command("settlement_report", stdout=out)
        result = json.loads(out.getvalue())
        self.assertEqual(result["reversal_reviews"], 1)
        self.assertTrue(result["incomplete"])
        self.assertIsNone(result["position"])
        alerted.assert_called_once()
        self.assertIn("reversal reviews=1", str(alerted.call_args))

    def test_partial_return_cannot_be_refunded_by_new_bank_recovery(self):
        with patch("utility.alerts.alert"):
            apply_wema_credit(self.wallet, self._row(500), [self.PAYOUT_REF])
        with patch("utility.wema.confirm_transfer_status") as status, \
             patch("banking.client.Client.request") as current_bank:
            call_command("reconcile_9psb")
        status.assert_not_called()
        current_bank.assert_not_called()
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.PENDING)
        self.assertTrue(self.payout.meta["wema_reversal_quarantine"]["active"])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4000"))

    def test_indexed_hold_blocks_settlement_even_if_json_summary_is_missing(self):
        from wallet.services import settle_or_refund

        with patch("utility.alerts.alert"):
            apply_wema_credit(self.wallet, self._row(500), [self.PAYOUT_REF])
        self.payout.refresh_from_db()
        meta = dict(self.payout.meta or {})
        meta.pop("wema_reversal_quarantine", None)
        self.payout.meta = meta
        self.payout.save(update_fields=["meta"])

        result = settle_or_refund(
            self.payout,
            {"success": False, "pending": False, "status": "FAILED"},
        )

        self.assertEqual(result, "quarantined")
        self.assertEqual(Wallet.objects.get(user=self.user).balance,
                         Decimal("4000.00"))

    def test_previously_credited_return_is_held_and_all_transition_paths_refuse_it(self):
        from wallet.services import (reverse_transfer, settle_or_refund,
                                     settle_payout, settle_reserved_funding)

        # Simulate an older deployment having misclassified the returned payout
        # as ordinary funding before reversal correlation was introduced.
        settle_reserved_funding(
            "WEMA-CR-ALAT-REV-PART", Decimal("1000"), self.user)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))

        with patch("utility.alerts.alert"):
            self.assertIsNone(apply_wema_credit(
                self.wallet, self._row(1000), [self.PAYOUT_REF]))

        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.PENDING)
        self.assertTrue(((self.payout.meta or {})
                         .get("wema_reversal_quarantine") or {}).get("active"))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertIsNone(reverse_transfer(self.PAYOUT_REF))
        self.assertIsNone(settle_payout(self.PAYOUT_REF))
        self.assertEqual(settle_or_refund(
            self.payout, {"success": False, "pending": False, "status": "FAILED"}),
            "quarantined")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))

    def test_an_exact_return_still_reverses(self):
        self.assertIsNone(apply_wema_credit(self.wallet, self._row(1000),
                                            [self.PAYOUT_REF]))
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))

    def test_distinct_later_return_after_full_reversal_is_quarantined(self):
        self.assertIsNone(apply_wema_credit(
            self.wallet, self._row(1000), [self.PAYOUT_REF]))
        later = _tx("ALAT-REV-LATER", 200,
                    narration=f"REFUND {self.PAYOUT_REF}")

        with patch("utility.alerts.alert") as alerted:
            self.assertIsNone(apply_wema_credit(
                Wallet.objects.get(user=self.user), later, [self.PAYOUT_REF]))

        self.payout.refresh_from_db()
        marker = self.payout.meta["wema_reversal_quarantine"]
        self.assertTrue(marker["active"])
        self.assertEqual(marker["reason"], "payout_already_reversed")
        self.assertEqual(Wallet.objects.get(user=self.user).balance,
                         Decimal("5000.00"))
        self.assertTrue(Transaction.objects.filter(
            reference="WEMA-CR-ALAT-REV-LATER",
            transaction_status=Transaction.FAILED,
        ).exists())
        alerted.assert_called_once()

    def test_same_reference_amount_change_preserves_original_evidence(self):
        with patch("utility.alerts.alert") as alerted:
            self.assertIsNone(apply_wema_credit(
                self.wallet, self._row(500), [self.PAYOUT_REF]))
            self.assertIsNone(apply_wema_credit(
                self.wallet, self._row(600), [self.PAYOUT_REF]))

        self.payout.refresh_from_db()
        marker = self.payout.meta["wema_reversal_quarantine"]
        evidence = ReversalEvidence.objects.get(payout=self.payout)
        self.assertEqual(evidence.amount, Decimal("500.00"))
        self.assertEqual(evidence.initial_reason, "partial_amount")
        self.assertEqual(
            set(evidence.observations.values_list("amount", flat=True)),
            {Decimal("500.00"), Decimal("600.00")},
        )
        self.assertEqual(evidence.state, ReversalEvidence.CONFLICT)
        self.assertEqual(marker["reason"], "evidence_amount_changed")
        self.assertNotIn("evidence", marker)  # unbounded detail lives in indexed rows
        durable = Transaction.objects.get(reference="WEMA-CR-ALAT-REV-PART")
        self.assertEqual(durable.amount, Decimal("500.00"))
        self.assertEqual(len(alerted.call_args_list), 2)

    def test_reused_provider_reference_keeps_both_payouts_resolvable(self):
        from wallet.services import (resolve_reversal_quarantine,
                                     reversal_quarantine_evidence)

        with patch("utility.alerts.alert"):
            apply_wema_credit(self.wallet, self._row(500), [self.PAYOUT_REF])
        second = Transaction.objects.create(
            user=self.user, service="Transfer to BOB", amount=Decimal("1000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="ZTCHDEADBEEF0003", meta={"bank": "GTBank"},
        )
        reused = _tx(
            "ALAT-REV-PART", 500,
            narration=f"REFUND {second.reference}",
        )
        with patch("utility.alerts.alert"):
            apply_wema_credit(self.wallet, reused, [second.reference])

        case = ReversalEvidence.objects.get(provider_reference="ALAT-REV-PART")
        self.assertEqual(case.state, ReversalEvidence.CONFLICT)
        self.assertEqual(case.reason, "provider_reference_reused")
        self.assertEqual(
            set(case.associated_payouts.values_list("pk", flat=True)),
            {self.payout.pk, second.pk},
        )
        snapshot = reversal_quarantine_evidence(second, str(case.pk))
        self.assertEqual(
            set(snapshot["associated_payout_references"]),
            {self.payout.reference, second.reference},
        )
        self.assertEqual(
            reversal_quarantine_evidence(second, str(case.pk)), snapshot,
        )
        second.meta = {"bank": "GTBank"}
        second.save(update_fields=["meta"])
        from wallet.review_state import transaction_review_map
        self.assertEqual(transaction_review_map([second])[second.pk][0], "reversal")
        before = Wallet.objects.get(user=self.user).balance

        result = resolve_reversal_quarantine(
            second.reference,
            disposition="dismiss_duplicate_evidence",
            reason="Confirmed duplicate provider evidence",
            evidence_snapshot=snapshot,
            actor=self.user,
            approval_id=987654,
            confirmed_amount="500.00",
        )

        self.assertEqual(result["movement"], "0")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)
        resolution = ReversalEvidenceResolution.objects.get(approval_id=987654)
        self.assertEqual(resolution.payout, second)
        for payout in (self.payout, second):
            payout.refresh_from_db()
            self.assertFalse(payout.meta["wema_reversal_quarantine"]["active"])

    def test_resolution_rejects_invalid_approval_ids_without_db_error(self):
        from wallet.services import (resolve_reversal_quarantine,
                                     reversal_quarantine_evidence)

        with patch("utility.alerts.alert"):
            apply_wema_credit(self.wallet, self._row(500), [self.PAYOUT_REF])
        case = ReversalEvidence.objects.get(payout=self.payout)
        snapshot = reversal_quarantine_evidence(self.payout, str(case.pk))

        for approval_id in (2 ** 63, True, 1.2, "1.2", "-1", ""):
            with self.subTest(approval_id=approval_id):
                with self.assertRaisesMessage(ValueError, "valid approval id"):
                    resolve_reversal_quarantine(
                        self.payout.reference,
                        disposition="confirm_partial_return",
                        reason="Confirmed partial bank return",
                        evidence_snapshot=snapshot,
                        actor=self.user,
                        approval_id=approval_id,
                    )
        case.refresh_from_db()
        self.assertEqual(case.state, ReversalEvidence.ACTIVE)

    def test_many_return_rows_keep_payout_metadata_bounded(self):
        with patch("utility.alerts.alert"):
            for index in range(30):
                row = _tx(
                    f"ALAT-REV-MANY-{index}",
                    100 + index,
                    narration=f"REFUND {self.PAYOUT_REF}",
                )
                self.assertIsNone(apply_wema_credit(
                    Wallet.objects.get(user=self.user), row, [self.PAYOUT_REF]))

        self.payout.refresh_from_db()
        marker = self.payout.meta["wema_reversal_quarantine"]
        self.assertEqual(marker["active_count"], 30)
        self.assertNotIn("evidence", marker)
        self.assertNotIn("resolution_history", marker)
        self.assertLess(len(json.dumps(marker)), 1500)
        self.assertEqual(ReversalEvidence.objects.filter(payout=self.payout).count(), 30)

    def test_provider_success_after_automatic_refund_is_held_not_resettled(self):
        from wallet.services import settle_payout

        self.assertIsNone(apply_wema_credit(
            self.wallet, self._row(1000), [self.PAYOUT_REF]))
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        before = Wallet.objects.get(user=self.user).balance

        outcome = settle_payout(self.payout.reference)

        self.assertIsNone(outcome)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertTrue(self.payout.meta["wema_reversal_quarantine"]["active"])
        case = ReversalEvidence.objects.get(payout=self.payout)
        self.assertEqual(case.state, ReversalEvidence.CONFLICT)
        self.assertEqual(case.initial_reason, "exact_return")
        self.assertEqual(case.reason, "provider_success_after_refund")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)


class ReversalLookbackTests(TestCase):
    """The reference set the reversal matcher scans is bounded by age.

    Unbounded it grows for the life of the account and is substring-scanned
    against every polled credit row, so a reconcile sweep costs
    payouts-ever x credit-rows — fine today, the slowest thing in the cron later.
    """

    def setUp(self):
        self.user, _ = make_user("08030000557", "lookback@zitch.app", balance="1000")

    def _payout(self, ref, age_days, *, meta=None, amount="100"):
        return make_transaction_at(
            self.user, created=timezone.now() - timedelta(days=age_days),
            service="Transfer to ADA", amount=Decimal(amount),
            direction=Transaction.OUT, status=Transaction.SUCCESS,
            reference=ref, meta={"bank": "GTBank"} if meta is None else meta)

    def test_recent_set_covers_every_bank_payout_metadata_shape(self):
        from wallet.services import self_payout_references

        shapes = (
            {"bank": "GTBank"},
            {"wema_transfer": {"platform_reference": "WEMA-1"}},
            {"account": "0123456789"},
            {"recipient_account": "0123456789"},
            {"recipient_account_number": "0123456789"},
        )
        expected = set()
        for index, meta in enumerate(shapes):
            ref = f"ZTCHMETASHAPE{index}"
            self._payout(ref, 1, meta=meta)
            expected.add(ref)
        # Presence of a legacy key with no value is not bank-payout evidence per
        # is_bank_payout and must not turn an unrelated outbound row into one.
        self._payout("ZTCHMETAEMPTY", 1, meta={"account": ""})

        refs = set(self_payout_references(self.user))
        self.assertTrue(expected.issubset(refs))
        self.assertNotIn("ZTCHMETAEMPTY", refs)

    def test_recent_payouts_are_matched_and_ancient_ones_are_not(self):
        from wallet.services import self_payout_references

        self._payout("ZTCHRECENT01", 3)
        self._payout("ZTCHANCIENT1", 400)
        refs = self_payout_references(self.user)
        self.assertIn("ZTCHRECENT01", refs)
        self.assertNotIn("ZTCHANCIENT1", refs)

    def test_ancient_legacy_payout_is_resolved_from_the_credit_row(self):
        # Keep the normal recent list bounded, but resolve a quoted old reference
        # through the unique reference index.  This row deliberately has no
        # reversal keyword, proving safety does not depend on marker wording.
        from wallet.services import self_payout_references

        payout = self._payout(
            "ZTCHANCIENTACCOUNT", 400, meta={"account": "0123456789"}, amount="100")
        wallet = Wallet.objects.get(user=self.user)
        wallet.balance = Decimal("900")
        wallet.save(update_fields=["balance", "updated"])
        self.assertNotIn(payout.reference, self_payout_references(self.user))

        row = _tx("ALAT-LATE-RETURN", 100,
                  narration=f"NIP ADVICE {payout.reference}")
        self.assertIsNone(apply_wema_credit(wallet, row, self_refs=[]))
        payout.refresh_from_db()
        self.assertEqual(payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000.00"))
        self.assertEqual(Transaction.objects.get(
            reference="WEMA-CR-ALAT-LATE-RETURN").transaction_status,
            Transaction.FAILED)

    def test_ancient_generated_reference_is_found_inside_provider_prefix(self):
        # Provider narrations sometimes glue their own label to our reference.
        # The all-time indexed fallback must extract the generated 16-character
        # fragment rather than query for the larger "REV-ZTCH..." token.
        payout = self._payout(
            "ZTCHABCDEF012345", 400, meta={"account": "0123456789"}, amount="100")
        wallet = Wallet.objects.get(user=self.user)
        wallet.balance = Decimal("900")
        wallet.save(update_fields=["balance", "updated"])

        row = _tx("ALAT-LATE-PREFIX", 100,
                  narration=f"REV-{payout.reference}-BOUNCED")
        self.assertIsNone(apply_wema_credit(wallet, row, self_refs=[]))

        payout.refresh_from_db()
        self.assertEqual(payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000.00"))

    @override_settings(WEMA_REVERSAL_LOOKBACK_DAYS=500)
    def test_the_window_is_configurable(self):
        from wallet.services import self_payout_references

        self._payout("ZTCHANCIENT2", 400)
        self.assertIn("ZTCHANCIENT2", self_payout_references(self.user))
