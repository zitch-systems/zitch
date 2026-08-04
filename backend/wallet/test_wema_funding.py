"""Tests for Wema/ALAT wallet funding — the OTP provisioning endpoints and the
credit-reconciliation poller (ALAT has no inbound-credit webhook).

- Provisioning: create -> verify-otp mints + persists a NUBAN with a WEMA
  account_reference (mock flow, PAYMENT_PROVIDER=wema).
- Reconcile: reconcile_wema credits an inbound (creditType=='Credit') deposit
  once, is idempotent across re-polls, and ignores debits / zero rows.
"""
import json
from decimal import Decimal
from unittest.mock import patch

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from accounts.models import hash_identifier
from wallet.models import Transaction, Wallet, WemaProvisioningAttempt
from wallet.services import apply_wema_credit, wema_account_reference
from wallet.tests import make_user


def _tx(ref, amount, credit=True, **extra):
    row = {"referenceId": ref, "amount": amount,
           "creditType": "Credit" if credit else "Debit",
           "narration": extra.get("narration", "Transfer in"),
           "sender": extra.get("sender", "GTBANK / JOHN")}
    # Omitted status == settled (no regression for existing rows); pass status=
    # explicitly to exercise the Pending/Failed funding guard.
    if "status" in extra:
        row["status"] = extra["status"]
    return row


@override_settings(PAYMENT_PROVIDER="wema")
class WemaWalletProvisioningTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000123", "w@zitch.app")

    def _post(self, path, payload):
        return self.client.post(path, data=json.dumps({**payload, "access_token": self.token}),
                                content_type="application/json")

    def test_otp_flow_provisions_wema_account(self):
        r1 = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        self.assertEqual(r1.status_code, 200)
        b1 = r1.json()
        self.assertTrue(b1["success"])
        self.assertTrue(b1["tracking_id"])
        self.assertTrue(b1["using_bvn"])

        r2 = self._post("/api/wallet/wema/verify-otp/",
                        {"otp": "123456", "tracking_id": b1["tracking_id"],
                         "using_bvn": True, "bvn": "22222222222"})
        self.assertEqual(r2.status_code, 200)
        b2 = r2.json()
        self.assertTrue(b2["success"])
        self.assertTrue(b2["account_number"])

        w = Wallet.objects.get(user=self.user)
        self.assertEqual(w.account_number, b2["account_number"])
        self.assertEqual(w.account_reference, wema_account_reference(self.user))
        # The server-bound BVN lifts KYC; the echoed legacy field is not trusted.
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)

    def test_provisioned_account_name_falls_back_to_registered_name(self):
        # If the provider hands back an account with no holder name, the wallet must
        # still persist a name — the customer's registered legal name — so the
        # funding card is never nameless (which would block a bank transfer).
        r1 = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        tracking = r1.json()["tracking_id"]
        with patch("utility.wema.get_account_details",
                   return_value={"success": True, "account_number": "9911223344",
                                 "account_name": "   ", "bank_name": "Wema Bank"}):
            r2 = self._post("/api/wallet/wema/verify-otp/",
                            {"otp": "123456", "tracking_id": tracking,
                             "using_bvn": True, "bvn": "22222222222"})
        self.assertEqual(r2.status_code, 200)
        w = Wallet.objects.get(user=self.user)
        self.assertEqual(w.account_number, "9911223344")
        self.assertEqual(w.account_name, self.user.get_full_name())  # "Ada Eze"

    def test_verify_requires_otp_and_tracking(self):
        r = self._post("/api/wallet/wema/verify-otp/", {"otp": "", "tracking_id": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn("OTP", r.json()["message"])

    def test_resend_otp_ok(self):
        tracking = self._post("/api/wallet/wema/create/",
                              {"bvn": "22222222222"}).json()["tracking_id"]
        r = self._post("/api/wallet/wema/resend-otp/",
                       {"tracking_id": tracking, "using_bvn": False})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["success"])

    def test_initiation_persists_only_a_keyed_identity_binding(self):
        raw = "22222222222"
        tracking = self._post("/api/wallet/wema/create/", {"bvn": raw}).json()["tracking_id"]
        attempt = WemaProvisioningAttempt.objects.get(user=self.user, tracking_id=tracking)
        self.assertEqual(attempt.identity_type, WemaProvisioningAttempt.BVN)
        self.assertEqual(attempt.identity_hash, hash_identifier(raw))
        self.assertEqual(attempt.identity_last4, "2222")
        self.assertNotEqual(attempt.identity_hash, raw)

    def test_restarting_active_flow_reuses_tracking_without_reusing_identity_at_wema(self):
        first = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        with patch("utility.wema.create_wallet_request") as start:
            second = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["tracking_id"], first.json()["tracking_id"])
        start.assert_not_called()

    def test_otp_cannot_be_used_to_verify_a_different_identity(self):
        tracking = self._post("/api/wallet/wema/create/",
                              {"bvn": "22222222222"}).json()["tracking_id"]
        response = self._post("/api/wallet/wema/verify-otp/", {
            "otp": "123456", "tracking_id": tracking, "using_bvn": False,
            "bvn": "33333333333",
        })
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertFalse(self.user.nin_verified)

    def test_verify_uses_server_bound_type_and_needs_no_identity_echo(self):
        tracking = self._post("/api/wallet/wema/create/",
                              {"nin": "12345678901"}).json()["tracking_id"]
        with patch("utility.wema.validate_wallet_otp", return_value={"success": True}) as validate:
            response = self._post("/api/wallet/wema/verify-otp/", {
                "otp": "123456", "tracking_id": tracking, "using_bvn": True,
            })
        self.assertEqual(response.status_code, 200)
        validate.assert_called_once_with(self.user.phone, "123456", tracking, bvn=False)
        self.user.refresh_from_db()
        self.assertTrue(self.user.nin_verified)
        self.assertEqual(self.user.nin_hash, hash_identifier("12345678901"))

    def test_identity_already_owned_by_another_user_is_rejected_before_wema(self):
        other, _ = make_user("08030000124", "other-wema@zitch.app")
        other.set_bvn("22222222222")
        other.bvn_verified = True
        other.save(update_fields=["bvn_hash", "bvn_last4", "bvn_verified"])
        with patch("utility.wema.create_wallet_request") as start:
            response = self._post("/api/wallet/wema/create/", {"bvn": "22222222222"})
        self.assertEqual(response.status_code, 409)
        start.assert_not_called()

    # --- The identity gate on an account the bank's callback provisioned first ---
    #
    # ALAT has no standalone BVN/NIN lookup, so the name match during provisioning IS
    # the identity check: the tier only lifts when the holder name the BANK returns
    # matches the registered name. When the Account Creation callback lands before the
    # customer finishes the OTP step — the common ordering — verify-otp used to
    # name-match against wallet.account_name. But provision_wema_account stores the
    # user's own registered name whenever the bank sent a blank nubanName, so that
    # compared the value to itself and passed unconditionally. These pin the fix: the
    # name is fetched from the bank by NUBAN, and a real mismatch holds the tier.

    def _verify_after_callback(self, kyc_status):
        """Run the real interleaving: the customer starts the OTP flow, the bank's
        Account Creation callback provisions the NUBAN with a blank nubanName while
        they are reading the SMS, and then they submit the code.

        Order matters — /create/ short-circuits once an account exists, so the
        tracking id has to be taken before the callback lands.
        """
        from wallet.services import provision_wema_account
        tracking = self._post("/api/wallet/wema/create/",
                              {"bvn": "22222222222"}).json()["tracking_id"]
        wallet, outcome = provision_wema_account(
            self.user, account_number="9911223344", account_name="",
            bank_name="Wema Bank", source="callback")
        self.assertEqual(outcome, "provisioned")
        # The stored name is our own fallback, so it is worthless as evidence —
        # matching it against the registered name can only ever pass.
        self.assertEqual(wallet.account_name, self.user.get_full_name())
        # wema_live gates the name match, so it has to be on — which also routes OTP
        # validation down the live path, hence the stub.
        with patch("utility.wema.wema_live", return_value=True), \
             patch("utility.wema.validate_wallet_otp", return_value={"success": True}), \
             patch("utility.wema.get_kyc_status", return_value=kyc_status):
            return self._post("/api/wallet/wema/verify-otp/",
                              {"otp": "123456", "tracking_id": tracking,
                               "using_bvn": True, "bvn": "22222222222"})

    def test_callback_provisioned_account_matches_the_name_the_bank_holds(self):
        r = self._verify_after_callback({"success": True, "name": "EZE ADA CHIDINMA"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.bvn_verified)   # bank's name matches -> tier lifts

    def test_callback_provisioned_account_holds_tier_on_a_real_mismatch(self):
        # Same request, but the NUBAN belongs to someone else at the bank. Before the
        # fix this lifted the tier: the comparison never saw this name at all.
        r = self._verify_after_callback({"success": True, "name": "JOHN DOE"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)
        self.assertEqual(self.user.tier, 1)       # unchanged, held for review

    def test_unreadable_bank_name_holds_kyc_for_review(self):
        r = self._verify_after_callback({"success": False})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)

    def test_account_create_starts_otp_flow_on_wema(self):
        # The app's existing "Get my account" endpoint must drive the Wema OTP
        # round-trip (Wema is the sole funding rail).
        r = self._post("/api/wallet/account/create/", {"bvn": "22222222222"})
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertTrue(b["success"])
        self.assertTrue(b["otp_required"])
        self.assertTrue(b["tracking_id"])
        self.assertTrue(b["using_bvn"])


@override_settings(PAYMENT_PROVIDER="wema")
class WemaAlatFundingTests(TestCase):
    """Fund from the user's own ALAT account (Pay with Bank Account) + NUBAN statement."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000321", "alat@zitch.app")

    def _post(self, path, payload):
        return self.client.post(path, data=json.dumps({**payload, "access_token": self.token}),
                                content_type="application/json")

    def test_statement_returns_normalized_rows(self):
        w = Wallet.objects.get(user=self.user)
        w.account_number = "0155500011"
        w.save(update_fields=["account_number"])
        with patch("utility.wema.get_transactions", return_value={"success": True, "transactions": [
                {"referenceId": "D1", "amount": "2500", "creditType": "Credit", "status": "Successfull",
                 "narration": "in", "transactionDate": "2026-07-01"}]}):
            r = self._post("/api/wallet/statement/", {})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["account_number"], "0155500011")
        self.assertEqual(body["transactions"][0]["reference"], "D1")
        self.assertTrue(body["transactions"][0]["is_credit"])

    def test_statement_needs_account(self):
        r = self._post("/api/wallet/statement/", {})
        self.assertEqual(r.status_code, 404)


class WemaReconcileTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08030000999", "r@zitch.app")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0155500011"
        self.wallet.account_reference = wema_account_reference(self.user)
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save(update_fields=["account_number", "account_reference", "bank_name"])

    def _run(self, txns):
        with patch("utility.wema.get_transactions",
                   return_value={"success": True, "transactions": txns}):
            call_command("reconcile_wema")

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

    def test_pending_or_failed_credit_row_not_funded(self):
        # ALAT TransactionStatus {Default, Successfull, Failed, Pending}. A settled
        # credit funds the wallet; a Pending (in-flight) or Failed (bounced) credit
        # row must be skipped so a deposit is never credited before it settles.
        self.assertIsNotNone(apply_wema_credit(self.wallet,
            {"referenceId": "S1", "amount": 700, "creditType": "Credit", "status": "Successfull"}))
        self.assertIsNone(apply_wema_credit(self.wallet,
            {"referenceId": "P1", "amount": 700, "creditType": "Credit", "status": "Pending"}))
        self.assertIsNone(apply_wema_credit(self.wallet,
            {"referenceId": "F1", "amount": 700, "creditType": "Credit", "status": "Failed"}))
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

    def test_reversed_credit_row_not_funded(self):
        # Defense-in-depth beyond the documented enum: normalize_transaction's
        # _TX_UNSETTLED also treats a re-spelled non-final status (e.g. Reversed) as
        # unsettled, so such a row is skipped too.
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
        with patch("utility.wema.get_transactions",
                   return_value={"success": True, "transactions": txns}), \
             patch("utility.wema.confirm_transfer_status",
                   return_value={"success": True, "status": status}):
            call_command("reconcile_wema", "--payout-older-than-minutes=0")

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
        self.assertFalse(Transaction.objects.filter(reference="WEMA-CR-ALAT-REV-77").exists())
        # Re-poll of the same window: the row matches again, but reverse_transfer
        # is a no-op on the already-FAILED payout.
        self._run([self._bounce()])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))

    def test_already_reversed_payout_not_credited_again(self):
        # Phase 2 (payout poller) reversed it in an earlier run…
        from wallet.services import reverse_transfer
        reverse_transfer(self.PAYOUT_REF)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        # …then the bounce appears in the polled history: no second credit.
        self._run([self._bounce()])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertFalse(Transaction.objects.filter(reference="WEMA-CR-ALAT-REV-77").exists())

    def test_reversal_matched_in_any_field(self):
        # Some rails echo the reference in referenceId rather than narration.
        row = _tx(self.PAYOUT_REF, 1000, narration="TRF REVERSAL")
        self._run([row])
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.transaction_status, Transaction.FAILED)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("5000.00"))
        self.assertFalse(Transaction.objects.filter(
            reference=f"WEMA-CR-{self.PAYOUT_REF}").exists())

    def test_unmatched_reversal_marker_is_quarantined(self):
        # Banks do not always echo our payout reference on a returned transfer.
        # With an outbound payout on this wallet, an explicit reversal marker is
        # held for manual reconciliation instead of being credited as funding.
        row = _tx("ALAT-REV-UNKNOWN", 1000,
                  narration="TRANSFER REVERSAL - ORIGINAL REFERENCE UNAVAILABLE")
        self.assertIsNone(apply_wema_credit(self.wallet, row, [self.PAYOUT_REF]))
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("4000.00"))
        self.assertFalse(Transaction.objects.filter(
            reference="WEMA-CR-ALAT-REV-UNKNOWN").exists())
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


@override_settings(PAYOUT_PROVIDER="wema")
class WemaPayoutSettlementTests(TestCase):
    """Wema has no payout webhook, so reconcile_wema must settle/reverse PENDING
    bank payouts by polling confirm_transfer_status."""

    def setUp(self):
        self.user, _ = make_user("08030000777", "p@zitch.app", balance="5000")
        self.wallet = Wallet.objects.get(user=self.user)

    def _pending_payout(self, ref):
        # Mirrors execute_payout's PENDING row: OUT + reconcile + a bank in meta.
        return Transaction.objects.create(
            user=self.user, service="Transfer to ADA", amount=Decimal("1000"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference=ref, meta={"reconcile": True, "bank": "Wema Bank"})

    def _run(self, status):
        with patch("utility.wema.confirm_transfer_status",
                   return_value={"success": True, "status": status}):
            call_command("reconcile_wema", "--payout-older-than-minutes=0")

    def test_pending_payout_settles_on_success(self):
        txn = self._pending_payout("ZTRF-PAY-OK")
        self._run("SUCCESS")
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.SUCCESS)

    def test_pending_payout_reverses_on_failure(self):
        txn = self._pending_payout("ZTRF-PAY-BAD")
        before = Wallet.objects.get(user=self.user).balance
        self._run("FAILED")
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        # Failed payout is refunded to the wallet.
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before + Decimal("1000"))

    def test_unknown_status_left_pending(self):
        txn = self._pending_payout("ZTRF-PAY-WAIT")
        self._run("IN_PROGRESS")
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)


@override_settings(PAYMENT_PROVIDER="wema")
class AdoptExistingWemaAccountTests(TestCase):
    """The bank refuses to create a customer it already holds ("customer records
    already exist"). Retrying asks for the same creation and is refused the same
    way, so without a fetch-and-adopt path a user in that state can never finish
    setup — which is exactly where a cleared test-mode NUBAN leaves them."""

    def setUp(self):
        self.client = Client()
        self.user, self.token = make_user("08030000555", "adopt@zitch.app")

    def _create(self, **payload):
        return self.client.post(
            "/api/wallet/wema/create/",
            data=json.dumps({**payload, "access_token": self.token}),
            content_type="application/json")

    def test_an_already_onboarded_customer_gets_the_existing_account(self):
        refusal = {"success": False, "message": "Customer records already exist"}
        existing = {"success": True, "account_number": "0123456789",
                    "account_name": "ADA EZE", "bank_name": "Wema Bank"}
        with patch("utility.wema.create_wallet_request", return_value=refusal), \
             patch("utility.wema.get_account_details", return_value=existing) as fetch, \
             patch("utility.wema.lift_debit_restriction",
                   return_value={"success": True}) as pnd:
            res = self._create(bvn="22222222222")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["account_number"], "0123456789")
        # Looked up by the user's OWN phone — this adopts that customer's account.
        self.assertEqual(fetch.call_args[0][0], self.user.phone)
        # An adopted NUBAN may still carry the Post-No-Debit hold, and payouts debit it.
        pnd.assert_called_once()
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "0123456789")

    def test_adopting_does_not_lift_the_kyc_tier(self):
        # No OTP was answered here, so nothing attests the identity. The user gets a
        # funding account; the tier still needs the normal flow.
        refusal = {"success": False, "message": "Customer records already exist"}
        existing = {"success": True, "account_number": "0123456789",
                    "account_name": "ADA EZE", "bank_name": "Wema Bank"}
        with patch("utility.wema.create_wallet_request", return_value=refusal), \
             patch("utility.wema.get_account_details", return_value=existing), \
             patch("utility.wema.lift_debit_restriction", return_value={"success": True}):
            self._create(bvn="22222222222")
        self.user.refresh_from_db()
        self.assertFalse(self.user.bvn_verified)

    def test_other_failures_are_still_reported(self):
        # Only "already exists" means there is something to adopt.
        with patch("utility.wema.create_wallet_request",
                   return_value={"success": False, "message": "Service unavailable"}), \
             patch("utility.wema.get_account_details") as fetch:
            res = self._create(bvn="22222222222")
        self.assertEqual(res.status_code, 502)
        self.assertIn("Service unavailable", res.json()["message"])
        fetch.assert_not_called()

    def test_an_already_onboarded_customer_with_no_readable_account_reports_the_refusal(self):
        with patch("utility.wema.create_wallet_request",
                   return_value={"success": False, "message": "Customer records already exist"}), \
             patch("utility.wema.get_account_details",
                   return_value={"success": False, "message": "not found"}):
            res = self._create(bvn="22222222222")
        self.assertEqual(res.status_code, 502)
        self.assertEqual(Wallet.objects.get(user=self.user).account_number, "")
