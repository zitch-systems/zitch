"""Search evidence identifies recovery work without posting or reversing money."""
import hashlib
import json
import re
from io import StringIO
from tempfile import NamedTemporaryFile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from wallet.models import Transaction, TransactionAlertDelivery, Wallet
from wallet.services import credit
from wema_vas.contracts import InvalidPayload, search_findings, search_request
from wema_vas.models import Receipt, VirtualAccount
from wema_vas.reconciliation import reconcile_snapshot
from wema_vas.services import process_notification
from wema_vas.tests import LIVE, account_fixture, payload


@override_settings(WEMA_VAS=LIVE, TESTING=True, TXN_ALERTS={})
class SearchReconciliationTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()

    def snapshot(self, **changes):
        body = payload(self.account)
        body.pop("created_at")
        body.update(requestdate="2026-01-20", nibssresponse="00", sendresponse="00")
        return {"status": "00", "status_desc": "1 Row(s) returned", "transactions": [{**body, **changes}]}

    def reconcile(self, **changes):
        return reconcile_snapshot(self.snapshot(**changes), session_id="SESSION-1")

    def test_request_is_exclusively_inbound_session_or_account(self):
        self.assertEqual(search_request(session_id="BANK-SESSION"), {"sessionid": "BANK-SESSION"})
        self.assertEqual(search_request(account=self.account.number), {"craccount": self.account.number})
        for values in ({}, {"session_id": "S", "account": self.account.number},
                       {"account": "711abcdefg"}, {"session_id": True}):
            with self.subTest(values=values), self.assertRaises(InvalidPayload):
                search_request(**values)

    def test_search_specific_status_rules_override_generic_portal_failed_shorthand(self):
        for nibss, acknowledgement, expected in (
            ("00", "00", "acknowledged"), ("00", "07", "notification_repush_required"),
            ("00", "", "notification_repush_required"), ("99", "00", "uncertain_contact_bank"),
            ("01", "07", "uncertain_contact_bank"),
        ):
            with self.subTest(nibss=nibss, acknowledgement=acknowledgement):
                rows = search_findings(self.snapshot(nibssresponse=nibss, sendresponse=acknowledgement))
                self.assertEqual(rows[0]["outcome"], expected)

    def test_malformed_amounts_or_incomplete_financial_identity_are_rejected(self):
        for value in ("0", "-10", "1.001", "NaN", 1.0, "1e5"):
            with self.subTest(value=value), self.assertRaises(InvalidPayload):
                self.reconcile(amount=value)
        for field in ("paymentreference", "originatoraccountnumber", "requestdate", "bankname"):
            snapshot = self.snapshot()
            del snapshot["transactions"][0][field]
            with self.subTest(field=field), self.assertRaises(InvalidPayload):
                reconcile_snapshot(snapshot, session_id="SESSION-1")

    def test_unsuccessful_envelopes_and_unbounded_snapshots_are_not_evidence(self):
        for body in ({"status": "07", "transactions": []}, {"status": "00", "transactions": {}}, []):
            with self.assertRaises(InvalidPayload):
                reconcile_snapshot(body, session_id="SESSION-1")
        body = self.snapshot()
        body["transactions"] *= 5001
        with self.assertRaises(InvalidPayload):
            reconcile_snapshot(body, account=self.account.number)

    def test_duplicate_session_or_payment_rows_fail_before_comparing(self):
        for changes in ({}, {"sessionid": "SECOND"}, {"paymentreference": "SECOND"}):
            snapshot = self.snapshot()
            snapshot["transactions"].append({**snapshot["transactions"][0], **changes})
            with self.subTest(changes=changes), self.assertRaises(InvalidPayload):
                reconcile_snapshot(snapshot, account=self.account.number)

    def test_query_scope_binding_rejects_unrelated_bank_rows(self):
        for scope in ({"session_id": "OTHER"}, {"account": "9999999999"}):
            with self.assertRaises(InvalidPayload):
                reconcile_snapshot(self.snapshot(), **scope)

    def test_matching_rows_do_not_claim_full_history_or_source_authentication(self):
        process_notification(payload(self.account))
        report = self.reconcile()
        self.assertEqual(report["status"], "supplied_rows_match")
        self.assertEqual(report["counts"], {"matched_in_supplied_rows": 1})
        self.assertFalse(report["source_authenticated"])
        self.assertFalse(report["scope_complete"])
        self.assertFalse(report["full_reconciliation_confirmed"])
        # Search requestdate is documented independently of notification
        # created_at. A date-only value must not invent a timestamp conflict.
        self.assertFalse(report["action_required"])

    def test_reconciliation_and_missing_notification_never_write_money_or_alerts(self):
        statements = []
        def read_only(execute, sql, params, many, context):
            statements.append(sql)
            self.assertIsNone(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE)\b", sql, re.I))
            return execute(sql, params, many, context)
        with connection.execute_wrapper(read_only):
            report = self.reconcile()
        self.assertTrue(statements)
        self.assertEqual(report["findings"][0]["local_comparison"], "missing_receipt_request_bank_repush")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 0)
        self.assertFalse(Receipt.objects.exists())
        self.assertFalse(Transaction.objects.exists())
        self.assertFalse(TransactionAlertDelivery.objects.exists())

    def test_uncertain_search_is_never_treated_as_failed_refundable_money(self):
        process_notification(payload(self.account))
        before = Wallet.objects.get(user=self.user).balance
        report = self.reconcile(nibssresponse="99")
        self.assertEqual(report["findings"][0]["local_comparison"], "bank_status_uncertain_contact_support")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)
        self.assertEqual(Transaction.objects.get().transaction_status, Transaction.SUCCESS)

    def test_unacknowledged_but_credited_notification_needs_safe_repush(self):
        process_notification(payload(self.account))
        report = self.reconcile(sendresponse="07")
        self.assertEqual(report["findings"][0]["local_comparison"], "bank_ack_discrepancy_request_repush")
        self.assertEqual(Transaction.objects.count(), 1)

    def test_held_receipts_stay_held_when_bank_search_is_successful(self):
        VirtualAccount.objects.filter(pk=self.account.pk).update(active=False)
        process_notification(payload(self.account))
        report = self.reconcile()
        self.assertEqual(report["findings"][0]["local_comparison"], "held_funds_manual_review")
        self.assertEqual(Receipt.objects.get().state, Receipt.HELD)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, 0)

    def test_financial_identity_and_cross_session_reference_conflicts_require_review(self):
        process_notification(payload(self.account))
        for changes in ({"amount": "500.00"}, {"paymentreference": "OTHER"},
                        {"originatoraccountnumber": "1111111111"}, {"craccountname": "Zitch/Other"},
                        {"originatorname": "Different Sender"}, {"bankcode": "999999"}):
            with self.subTest(changes=changes):
                report = self.reconcile(**changes)
                self.assertEqual(report["findings"][0]["local_comparison"], "financial_identity_conflict_manual_review")
        report = reconcile_snapshot(self.snapshot(sessionid="SECOND"), session_id="SECOND")
        self.assertEqual(report["findings"][0]["local_comparison"], "reference_conflict_manual_review")

    def test_broken_ledger_binding_is_not_a_successful_match(self):
        process_notification(payload(self.account))
        Transaction.objects.update(transaction_status=Transaction.FAILED)
        report = self.reconcile()
        self.assertEqual(report["findings"][0]["local_comparison"], "ledger_binding_invalid_manual_review")

    def test_legacy_credit_does_not_satisfy_a_missing_vas_receipt(self):
        credit(self.user, "1250.00", "funding", reference="legacy-funding", meta={"provider": "wema"})
        report = self.reconcile()
        self.assertEqual(report["findings"][0]["local_comparison"], "missing_receipt_request_bank_repush")
        self.assertEqual(Transaction.objects.count(), 1)

    def test_unmapped_or_validation_account_is_not_a_live_settlement_match(self):
        report = reconcile_snapshot(self.snapshot(craccount="9999999999"), account="9999999999")
        self.assertEqual(report["findings"][0]["local_comparison"], "unmapped_account_manual_review")
        _, sample = account_fixture("2", mode="validation")
        report = reconcile_snapshot(self.snapshot(craccount=sample.number), account=sample.number)
        self.assertEqual(report["findings"][0]["local_comparison"], "validation_account_excluded")

    def test_report_redacts_names_accounts_and_session_payment_references(self):
        report = json.dumps(self.reconcile())
        for value in (self.account.number, self.account.display_name, "SESSION-1", "PAYMENT-1", "Test Sender", "0000000000"):
            self.assertNotIn(value, report)

    def test_report_omits_account_pseudonyms_and_keys_session_pseudonym(self):
        original = self.reconcile()["findings"][0]
        self.assertNotIn("account_fingerprint", original)
        self.assertNotEqual(original["session_fingerprint"], hashlib.sha256(b"SESSION-1").hexdigest()[:16])
        with override_settings(SECRET_KEY="different-test-report-secret"):
            rotated = self.reconcile()["findings"][0]
        self.assertNotEqual(original["session_fingerprint"], rotated["session_fingerprint"])

    def test_search_requestdate_is_not_assumed_to_be_notification_occurrence_time(self):
        process_notification(payload(self.account))
        report = self.reconcile(requestdate="2026-01-21T01:00:00Z")
        self.assertEqual(report["findings"][0]["local_comparison"], "matched_in_supplied_rows")

    def test_command_outputs_findings_without_sending_or_mutating_and_exits_for_review(self):
        output = StringIO()
        with NamedTemporaryFile(mode="w+", suffix=".json") as snapshot:
            json.dump(self.snapshot(), snapshot)
            snapshot.flush()
            with self.assertRaises(CommandError):
                call_command("vas_reconcile_snapshot", "--snapshot", snapshot.name, "--session-id", "SESSION-1", stdout=output)
        self.assertTrue(json.loads(output.getvalue())["read_only"])
        self.assertFalse(Transaction.objects.exists())

    def test_command_rejects_duplicate_json_keys_and_sanitizes_file_errors(self):
        with NamedTemporaryFile(mode="w+", suffix=".json") as snapshot:
            snapshot.write('{"status":"00","status":"07","transactions":[]}')
            snapshot.flush()
            with self.assertRaisesMessage(CommandError, "Invalid or unreadable"):
                call_command("vas_reconcile_snapshot", "--snapshot", snapshot.name, "--session-id", "SESSION-1")
        with self.assertRaises(CommandError) as failure:
            call_command("vas_reconcile_snapshot", "--snapshot", "/private-secret-path/missing.json", "--session-id", "SECRET-SESSION")
        self.assertNotIn("private-secret-path", str(failure.exception))
        self.assertNotIn("SECRET-SESSION", str(failure.exception))

    def test_empty_array_does_not_prove_complete_reconciliation(self):
        report = reconcile_snapshot({"status": "00", "transactions": []}, account=self.account.number)
        self.assertEqual(report["status"], "no_rows_in_supplied_snapshot")
        self.assertFalse(report["scope_complete"])
        self.assertFalse(report["full_reconciliation_confirmed"])
