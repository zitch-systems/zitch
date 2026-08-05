"""The compliance obligations the policy pack already promised a regulator.

Each test names the commitment it enforces, because that is the only thing that makes
these features falsifiable: "we monitor for structuring" is unreviewable, "a case exists
with a 30-day deadline and it goes red when missed" is not.
"""
import json
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from compliance.models import AmlCase, DataSubjectRequest, Dispute
from compliance.services import (close_case, erase_user_data, export_user_data,
                                 open_case, open_dispute, resolve_dispute,
                                 scan_transactions)
from wallet.models import Transaction, Wallet
from wallet.services import credit, debit
from wallet.tests import make_user


@override_settings(VELOCITY_MAX_OUT_10MIN=0, AML_THRESHOLD_NGN=Decimal("5000000"))
class AmlMonitoringTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08088880001", "aml@zitch.app",
                                          balance="20000000", tier=3)
        # These rules are about multi-million-naira movements, which the existing
        # ≥₦100,000 face gate refuses outright — so the subject has to be past it for the
        # monitoring to have anything to see.
        self.user.face_verified = True
        self.user.save(update_fields=["face_verified"])

    def test_a_threshold_breach_opens_a_case_with_a_30_day_deadline(self):
        # The policy: SARs filed with NFIU "within 30 days of detection".
        txn = debit(self.user, Decimal("5000000"), "Transfer to BIG CO")
        counts = scan_transactions()
        self.assertEqual(counts["threshold"], 1)
        case = AmlCase.objects.get()
        self.assertEqual(case.trigger, AmlCase.THRESHOLD)
        self.assertEqual(case.evidence["reference"], txn.reference)
        self.assertEqual((case.due - case.detected).days, AmlCase.SAR_DEADLINE_DAYS)

    def test_below_the_threshold_opens_nothing(self):
        debit(self.user, Decimal("4999999"), "Transfer to BIG CO")
        self.assertEqual(scan_transactions()["threshold"], 0)
        self.assertEqual(AmlCase.objects.count(), 0)

    def test_rescanning_does_not_duplicate_a_case(self):
        # The scanner is a cron with an overlapping window; a real finding must not drown
        # in copies of itself.
        debit(self.user, Decimal("5000000"), "Transfer to BIG CO")
        scan_transactions()
        scan_transactions()
        self.assertEqual(AmlCase.objects.filter(trigger=AmlCase.THRESHOLD).count(), 1)

    def test_structuring_is_caught_across_days(self):
        # Three transfers of ₦3m: each under the ₦5m threshold, together over it, on
        # different days. Spreading them is what evades the ₦5m DAILY cap — which is also
        # why the structuring window has to be multi-day to be reachable at all.
        for i in range(3):
            txn = debit(self.user, Decimal("3000000"), f"Transfer to SPLIT {i}")
            Transaction.objects.filter(pk=txn.pk).update(
                created=timezone.now() - timedelta(days=i + 1))
        counts = scan_transactions()
        self.assertEqual(counts["structuring"], 1)
        case = AmlCase.objects.get(trigger=AmlCase.STRUCTURING)
        self.assertEqual(case.evidence["rows"], 3)
        self.assertEqual(Decimal(case.evidence["total"]), Decimal("9000000"))

    def test_a_24_hour_structuring_window_would_be_unreachable(self):
        # Pinning the finding, not just the fix: with a one-day window the rule cannot
        # fire, because a Tier-3 customer's whole day is capped at the threshold itself.
        # If someone "tidies" the window back to 24h, this fails and says why.
        from accounts.models import User
        self.assertLessEqual(User.DAILY_TRANSFER_LIMITS[3],
                             Decimal(str(settings.AML_THRESHOLD_NGN)))
        from compliance import services
        self.assertGreater(services.STRUCTURING_WINDOW_HOURS, 24)

    def test_ordinary_small_spending_is_not_structuring(self):
        # Amounts nowhere near the threshold are just spending; flagging them would bury
        # the queue in noise and train people to close cases unread.
        for i in range(20):
            debit(self.user, Decimal("5000"), f"Airtime {i}")
        self.assertEqual(scan_transactions()["structuring"], 0)

    @override_settings(AML_VELOCITY_MIN_ROWS_24H=5)
    def test_a_busy_day_opens_a_velocity_case(self):
        for i in range(6):
            debit(self.user, Decimal("1000"), f"Airtime {i}")
        with mock.patch("compliance.services.VELOCITY_MIN_ROWS", 5):
            self.assertEqual(scan_transactions()["velocity"], 1)

    def test_failed_rows_are_not_monitored(self):
        txn = debit(self.user, Decimal("5000000"), "Transfer to BIG CO")
        txn.transaction_status = Transaction.FAILED
        txn.save(update_fields=["transaction_status"])
        self.assertEqual(scan_transactions()["threshold"], 0)

    def test_monitoring_does_not_block_the_transaction(self):
        # The policy wording says a suspicious transaction is "blocked pending
        # investigation". The code flags and does not block — deliberately, and
        # documented in docs/compliance-operations.md: auto-blocking at exactly the
        # Tier-3 cap would refuse legitimate top-tier transfers. This test pins the
        # actual behaviour so the divergence cannot be discovered by accident.
        txn = debit(self.user, Decimal("5000000"), "Transfer to BIG CO")
        scan_transactions()
        self.assertEqual(Transaction.objects.get(pk=txn.pk).transaction_status,
                         Transaction.PENDING)
        self.assertTrue(AmlCase.objects.exists())


class AmlCaseClosureTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088880002", "aml2@zitch.app")
        self.case = open_case(self.user, AmlCase.THRESHOLD, evidence={"amount": "6000000"})

    def test_closing_requires_a_narrative(self):
        # A dismissal with no reason is indistinguishable from an unworked case.
        with self.assertRaises(ValueError):
            close_case(self.case, status=AmlCase.DISMISSED, narrative="  ")

    def test_filing_requires_the_nfiu_reference(self):
        # Otherwise "we filed" is an assertion with nothing behind it.
        with self.assertRaises(ValueError):
            close_case(self.case, status=AmlCase.FILED, narrative="Filed with goAML")

    def test_filing_records_whether_it_was_on_time(self):
        close_case(self.case, status=AmlCase.FILED, narrative="Filed with goAML",
                   nfiu_reference="GOAML-2026-0001")
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, AmlCase.FILED)
        from whatsapp.models import AuditLog
        row = AuditLog.objects.filter(action="aml.case_filed").first()
        self.assertTrue(row.after["filed_on_time"])

    def test_an_open_case_past_its_deadline_is_overdue(self):
        self.case.due = timezone.now() - timedelta(days=1)
        self.case.save(update_fields=["due"])
        self.assertTrue(self.case.overdue)
        close_case(self.case, status=AmlCase.DISMISSED, narrative="Reviewed, legitimate")
        self.assertFalse(self.case.overdue)   # closed cases are not a live breach

    def test_the_scan_command_pages_on_an_overdue_case(self):
        self.case.due = timezone.now() - timedelta(days=1)
        self.case.save(update_fields=["due"])
        out, err = StringIO(), StringIO()
        code = 0
        with mock.patch("utility.alerts.alert") as alert_mock:
            try:
                call_command("aml_scan", "--fail-on-overdue", stdout=out, stderr=err)
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(code, 1)
        self.assertIn("OVERDUE", err.getvalue())
        self.assertTrue(any("filing deadline" in c[0][0] for c in alert_mock.call_args_list))


class DataSubjectExportTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08088880003", "dsr@zitch.app", balance="5000")
        self.user.first_name = "Ada"
        self.user.last_name = "Okafor"
        self.user.address = "12 Marina, Lagos"
        self.user.bvn_hash = "deadbeef"
        self.user.bvn_last4 = "1234"
        self.user.bvn_verified = True
        self.user.save()

    def test_the_export_covers_every_pii_holding_area(self):
        data = export_user_data(self.user)
        for section in ("account", "wallet", "transactions", "linked_banks", "cards",
                        "savings", "loans", "whatsapp", "whatsapp_messages", "disputes",
                        "known_devices"):
            self.assertIn(section, data)
        self.assertEqual(data["account"]["email"], "dsr@zitch.app")
        self.assertEqual(data["account"]["address"], "12 Marina, Lagos")
        self.assertTrue(data["transactions"])

    def test_the_export_never_contains_a_verification_hash(self):
        # The hash is not reversible but it IS a stable identifier — the same BVN always
        # produces it — so handing it out would let the holder re-identify the subject.
        blob = json.dumps(export_user_data(self.user))
        self.assertNotIn("deadbeef", blob)
        # It says what is held, without handing it over.
        self.assertIn("verification hash", export_user_data(self.user)["notice"])

    def test_the_export_is_json_serialisable(self):
        # Decimals and datetimes are the two things that break this in practice.
        json.dumps(export_user_data(self.user))

    def test_the_command_refuses_an_ambiguous_identifier(self):
        make_user("08088880004", "dsr@zitch.app")   # same email, second account
        with self.assertRaises(CommandError):
            call_command("data_subject_request", "--user", "dsr@zitch.app", "--export",
                         stdout=StringIO())

    def test_the_command_records_the_request(self):
        call_command("data_subject_request", "--user", str(self.user.pk), "--export",
                     "--note", "TICKET-9", stdout=StringIO())
        req = DataSubjectRequest.objects.get()
        self.assertEqual(req.kind, DataSubjectRequest.EXPORT)
        self.assertEqual(req.status, DataSubjectRequest.COMPLETED)
        self.assertEqual(req.note, "TICKET-9")
        self.assertEqual((req.due - req.received).days, DataSubjectRequest.RESPONSE_DAYS)


class DataSubjectErasureTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08088880005", "erase@zitch.app", balance="7000")
        self.user.first_name = "Ada"
        self.user.address = "12 Marina, Lagos"
        self.user.bvn_hash = "deadbeef"
        self.user.bvn_last4 = "1234"
        self.user.save()

    def test_erasure_clears_identifiers_and_keeps_the_ledger(self):
        # The whole tension: NDPR erasure vs a 5+ year retention commitment. Resolved by
        # pseudonymising, not deleting.
        before = Transaction.objects.filter(user=self.user).count()
        self.assertTrue(before)
        outcome = erase_user_data(self.user)
        self.user.refresh_from_db()

        self.assertEqual(self.user.first_name, "")
        self.assertIsNone(self.user.phone)
        self.assertEqual(self.user.email, "")
        self.assertEqual(self.user.address, "")
        self.assertEqual(self.user.bvn_hash, "")
        self.assertEqual(self.user.bvn_last4, "")
        self.assertFalse(self.user.is_active)
        self.assertEqual(self.user.username, outcome["pseudonym"])
        # Retained.
        self.assertEqual(Transaction.objects.filter(user=self.user).count(), before)
        self.assertIn("ledger_transactions", outcome["retained"])

    def test_erasure_kills_the_session(self):
        # A live token would keep the "erased" account reachable as if nothing happened.
        from accounts.models import AccessToken
        self.assertIsNotNone(AccessToken.resolve(self.token))
        erase_user_data(self.user)
        self.assertIsNone(AccessToken.resolve(self.token))

    def test_two_erasures_do_not_collide_on_the_unique_phone(self):
        other, _ = make_user("08088880006", "erase2@zitch.app")
        erase_user_data(self.user)
        erase_user_data(other)   # phone must go to NULL, not ""
        self.assertEqual(DataSubjectRequest.objects.count(), 0)  # recorded by the caller

    def test_the_command_will_not_erase_without_confirmation(self):
        code = 0
        try:
            call_command("data_subject_request", "--user", str(self.user.pk), "--erase",
                         stdout=StringIO())
        except SystemExit as exc:
            code = exc.code
        self.assertEqual(code, 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "erase@zitch.app")

    def test_the_command_erases_with_confirmation_and_records_it(self):
        call_command("data_subject_request", "--user", str(self.user.pk), "--erase",
                     "--confirm", "--note", "NDPR-4", stdout=StringIO())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        req = DataSubjectRequest.objects.get()
        self.assertEqual(req.kind, DataSubjectRequest.ERASE)
        self.assertIn("cleared_fields", req.outcome)
        # The subject label survives the erasure — after one, it is the only handle left.
        self.assertEqual(req.subject_label, "erase@zitch.app")


class DisputeTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08088880007", "disp@zitch.app", balance="50000")
        self.txn = debit(self.user, Decimal("5000"), "Transfer to ADEYEMI")
        self.other, self.other_token = make_user("08088880008", "disp2@zitch.app")

    def _post(self, path, body, token=None):
        return self.client.post(
            f"/api/disputes/{path}", data=json.dumps(body),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self.token}")

    def test_a_customer_can_dispute_their_own_transaction(self):
        res = self._post("open/", {"reference": self.txn.reference,
                                   "reason": "not_received",
                                   "detail": "Recipient says nothing arrived"})
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], Dispute.OPEN)
        dispute = Dispute.objects.get()
        self.assertEqual((dispute.due - dispute.created).days, Dispute.RESPONSE_DAYS)

    def test_a_dispute_moves_no_money(self):
        # A customer-raised form that refunded itself would be a free-money button.
        before = Wallet.objects.get(user=self.user).balance
        self._post("open/", {"reference": self.txn.reference, "reason": "not_received"})
        self.assertEqual(Wallet.objects.get(user=self.user).balance, before)

    def test_a_second_tap_does_not_open_a_second_case(self):
        self._post("open/", {"reference": self.txn.reference, "reason": "not_received"})
        self._post("open/", {"reference": self.txn.reference, "reason": "not_received"})
        self.assertEqual(Dispute.objects.count(), 1)

    def test_another_customers_reference_is_refused(self):
        # And refused the same way a nonexistent one is: otherwise the endpoint confirms
        # whether an arbitrary reference exists, which is a lookup oracle over every
        # customer's transaction ids.
        res = self._post("open/", {"reference": self.txn.reference,
                                   "reason": "not_received"}, token=self.other_token)
        self.assertEqual(res.status_code, 400)
        missing = self._post("open/", {"reference": "ZTCH-NOPE",
                                       "reason": "not_received"}, token=self.other_token)
        self.assertEqual(missing.json()["message"], res.json()["message"])

    def test_an_unknown_reason_is_refused(self):
        res = self._post("open/", {"reference": self.txn.reference, "reason": "vibes"})
        self.assertEqual(res.status_code, 400)

    def test_a_customer_sees_only_their_own_disputes(self):
        self._post("open/", {"reference": self.txn.reference, "reason": "not_received"})
        rows = self._post("", {}, token=self.other_token).json()["rows"]
        self.assertEqual(rows, [])

    def test_a_customer_can_withdraw(self):
        dispute_id = self._post("open/", {"reference": self.txn.reference,
                                          "reason": "duplicate"}).json()["dispute_id"]
        res = self._post("withdraw/", {"dispute_id": dispute_id})
        self.assertEqual(res.json()["status"], Dispute.WITHDRAWN)
        # Withdrawn frees the reference, so a customer who withdrew by mistake can
        # re-raise rather than being locked out by the uniqueness constraint.
        again = self._post("open/", {"reference": self.txn.reference, "reason": "duplicate"})
        self.assertEqual(again.status_code, 200)

    def test_resolving_requires_a_reason(self):
        dispute = open_dispute(self.user, reference=self.txn.reference,
                               reason="wrong_amount")
        with self.assertRaises(ValueError):
            resolve_dispute(dispute, status=Dispute.DECLINED, resolution="   ")

    def test_resolution_records_whether_it_was_on_time(self):
        dispute = open_dispute(self.user, reference=self.txn.reference, reason="duplicate")
        resolve_dispute(dispute, status=Dispute.UPHELD, resolution="Duplicate confirmed")
        from whatsapp.models import AuditLog
        row = AuditLog.objects.filter(action="dispute.upheld").first()
        self.assertTrue(row.after["on_time"])


class DsrFromAdminTests(TestCase):
    """`manage.py data_subject_request` needs a production shell. A deploy without one
    (Render's free tier has none) could not service an NDPR request AT ALL — not an
    answer a regulator accepts for an obligation with a 30-day statutory clock. These
    are the same operations from the browser, keeping the command's security posture:
    the export never reaches the browser, and erasure needs two people."""

    def setUp(self):
        from django.contrib.admin.sites import AdminSite

        from accounts.admin import UserAdmin
        from accounts.models import User

        self.admin = UserAdmin(User, AdminSite())
        self.messages = []
        self.admin.message_user = lambda req, msg, level=None: self.messages.append((msg, level))
        self.subject = User.objects.create(username="0801000900", phone="0801000900",
                                           email="subject@zitch.test", first_name="Ada",
                                           last_name="Eze")
        self.op_a = User.objects.create(username="op-a", email="a@zitch.test", is_staff=True)
        self.op_b = User.objects.create(username="op-b", email="b@zitch.test", is_staff=True)

    def _req(self, actor):
        return type("R", (), {"user": actor})()

    def _subjects(self):
        from accounts.models import User

        return User.objects.filter(pk=self.subject.pk)

    def _said(self):
        return " ".join(m for m, _ in self.messages)

    # --- export ---------------------------------------------------------------
    @override_settings(COMPLIANCE_EXPORT_EMAIL="dpo@zitch.ng")
    def test_export_is_emailed_as_a_file_and_never_returned(self):
        with mock.patch("utility.providers.send_email",
                        return_value={"success": True}) as send:
            self.admin.dsr_export(self._req(self.op_a), self._subjects())
        to, subject, body = send.call_args[0]
        self.assertEqual(to, "dpo@zitch.ng")
        attachments = send.call_args[1]["attachments"]
        self.assertEqual(len(attachments), 1)
        payload = json.loads(attachments[0]["content"])
        self.assertTrue(payload)
        # The RECORD travels as a file. Naming the subject in the body and in the admin
        # message is intended — the DPO has to know whose export arrived, and that is
        # what subject_label is for. What must not travel loose is the data itself.
        self.assertNotIn("transactions", body.split("Sections:")[0])
        for blob in (body, self._said()):
            self.assertNotIn(json.dumps(payload["account"], ensure_ascii=False), blob)
        req = DataSubjectRequest.objects.get(kind=DataSubjectRequest.EXPORT)
        self.assertEqual(req.status, DataSubjectRequest.COMPLETED)
        self.assertEqual(req.requested_by, self.op_a)

    @override_settings(COMPLIANCE_EXPORT_EMAIL="")
    def test_export_refuses_rather_than_falling_back_to_the_browser(self):
        with mock.patch("utility.providers.send_email") as send:
            self.admin.dsr_export(self._req(self.op_a), self._subjects())
        send.assert_not_called()
        self.assertFalse(DataSubjectRequest.objects.exists())
        self.assertIn("COMPLIANCE_EXPORT_EMAIL", self._said())

    @override_settings(COMPLIANCE_EXPORT_EMAIL="dpo@zitch.ng")
    def test_a_failed_delivery_is_not_reported_as_delivered(self):
        with mock.patch("utility.providers.send_email",
                        return_value={"success": False, "message": "smtp down"}):
            self.admin.dsr_export(self._req(self.op_a), self._subjects())
        # The row stays: an examiner asks WHEN the export was run, not whether the mail
        # server was up. But the operator must not think it arrived.
        self.assertTrue(DataSubjectRequest.objects.filter(
            kind=DataSubjectRequest.EXPORT).exists())
        self.assertIn("email failed", self._said())
        self.assertIn("do not treat this as delivered", self._said())

    # --- erasure --------------------------------------------------------------
    def test_erasure_takes_two_people(self):
        self.admin.dsr_request_erasure(self._req(self.op_a), self._subjects())
        req = DataSubjectRequest.objects.get(kind=DataSubjectRequest.ERASE)
        self.assertEqual(req.status, DataSubjectRequest.RECEIVED)

        # The same operator cannot carry out their own request.
        self.admin.dsr_carry_out_erasure(self._req(self.op_a), self._subjects())
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.email, "subject@zitch.test")   # untouched
        self.assertIn("second operator", self._said())

        # A different one can.
        self.admin.dsr_carry_out_erasure(self._req(self.op_b), self._subjects())
        self.subject.refresh_from_db()
        self.assertNotEqual(self.subject.email, "subject@zitch.test")
        req.refresh_from_db()
        self.assertEqual(req.status, DataSubjectRequest.COMPLETED)
        self.assertTrue(req.completed)
        self.assertTrue(req.subject_label)      # the only handle left on the request

    def test_erasure_refuses_without_a_request(self):
        self.admin.dsr_carry_out_erasure(self._req(self.op_b), self._subjects())
        self.subject.refresh_from_db()
        self.assertEqual(self.subject.email, "subject@zitch.test")
        self.assertIn("no open erasure request", self._said())

    def test_a_second_request_does_not_stack(self):
        self.admin.dsr_request_erasure(self._req(self.op_a), self._subjects())
        self.admin.dsr_request_erasure(self._req(self.op_b), self._subjects())
        self.assertEqual(DataSubjectRequest.objects.filter(
            kind=DataSubjectRequest.ERASE).count(), 1)
        self.assertIn("already open", self._said())
