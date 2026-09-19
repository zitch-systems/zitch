"""Customer transaction rows stay non-terminal while provider evidence is held."""
import json
from decimal import Decimal

from django.test import TestCase

from accounts.models import AccessToken, User
from wallet.models import (FundingIntent, ReversalEvidence,
                           ReversalEvidenceObservation, Transaction)


class CustomerReviewStatusTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="review-status-customer", phone="08015556667",
            email="review-status@zitch.test",
        )
        self.token = AccessToken.issue(self.user).key

    def _post(self, path, body=None):
        return self.client.post(
            path,
            data=json.dumps({"access_token": self.token, **(body or {})}),
            content_type="application/json",
        )

    def _txn(self, reference, service, *, direction=Transaction.OUT, meta=None):
        return Transaction.objects.create(
            user=self.user, service=service, amount=Decimal("500"),
            direction=direction, transaction_status=Transaction.SUCCESS,
            reference=reference, meta=meta or {},
        )

    def test_history_and_live_status_hide_success_for_all_active_review_rails(self):
        reversal = self._txn("ZCUSTOMERREVREVIEW1", "Bank transfer",
                             meta={"bank": "Wema"})
        evidence = ReversalEvidence.objects.create(
            provider=ReversalEvidence.WEMA,
            provider_reference="ALAT-CUSTOMER-REVIEW-1",
            provider_reference_hash="b" * 64,
            user=self.user, payout=reversal, amount=Decimal("500"),
            initial_reason="provider_status_conflict",
            reason="provider_status_conflict", state=ReversalEvidence.ACTIVE,
        )
        ReversalEvidenceObservation.objects.create(
            evidence=evidence, amount=Decimal("500"))
        associated_reversal = self._txn(
            "ZCUSTOMERASSOCIATEDREV1", "Bank transfer",
            meta={"bank": "Wema"},
        )
        evidence.associated_payouts.add(associated_reversal)

        card = self._txn(
            "ZCUSTOMERCARDREVIEW1", "Card funding",
            meta={"card_funding": True, "card": 99,
                  "card_balance_review": True,
                  "card_balance_applied": False},
        )
        funding = self._txn(
            "ZCUSTOMERFUNDREVIEW1", "Wallet top-up",
            direction=Transaction.IN,
        )
        FundingIntent.objects.create(
            user=self.user, reference=funding.reference, amount=Decimal("500"),
            status=FundingIntent.PAID, credited=True,
            meta={"funding_review": {
                "active": True, "reason": "success_after_failed",
            }},
        )

        history = self._post("/api/user-transaction-history/")
        self.assertEqual(history.status_code, 200, history.content)
        rows = {
            row["reference"]: row
            for row in history.json()["all_site_transactions"]
        }
        expected = {
            reversal.reference: "reversal",
            associated_reversal.reference: "reversal",
            card.reference: "card_funding",
            funding.reference: "wallet_funding",
        }
        for reference, kind in expected.items():
            self.assertEqual(rows[reference]["transaction_status"],
                             Transaction.PENDING)
            self.assertTrue(rows[reference]["under_review"])
            self.assertEqual(rows[reference]["review_kind"], kind)
            self.assertIn("Do not retry", rows[reference]["status_message"])

            detail = self._post(
                "/api/transaction/status/", {"reference": reference})
            self.assertEqual(detail.status_code, 200, detail.content)
            self.assertEqual(
                detail.json()["transaction"]["transaction_status"],
                Transaction.PENDING,
            )
            self.assertTrue(detail.json()["transaction"]["under_review"])

    def test_resolved_evidence_restores_the_ledger_status(self):
        txn = self._txn("ZCUSTOMERRESOLVED1", "Bank transfer")
        evidence = ReversalEvidence.objects.create(
            provider=ReversalEvidence.WEMA,
            provider_reference="ALAT-CUSTOMER-RESOLVED-1",
            provider_reference_hash="c" * 64,
            user=self.user, payout=txn, amount=Decimal("500"),
            initial_reason="full_reversal", reason="full_reversal",
            state=ReversalEvidence.RESOLVED,
            resolved_amount=Decimal("500"),
            resolved_at=txn.created,
        )
        ReversalEvidenceObservation.objects.create(
            evidence=evidence, amount=Decimal("500"))

        detail = self._post(
            "/api/transaction/status/", {"reference": txn.reference})
        row = detail.json()["transaction"]
        self.assertEqual(row["transaction_status"], Transaction.SUCCESS)
        self.assertFalse(row["under_review"])
        self.assertEqual(row["review_kind"], "")

    def test_review_state_lookup_is_bulk_not_one_query_per_history_row(self):
        from wallet.review_state import transaction_review_map

        rows = [self._txn(f"ZBULKREVIEW{i:03d}", "Airtime") for i in range(20)]
        with self.assertNumQueries(2):
            self.assertEqual(transaction_review_map(rows), {})

    def test_excel_statement_labels_an_active_review_instead_of_success(self):
        import io
        import zipfile
        from unittest.mock import patch

        txn = self._txn("ZCUSTOMERSTATEMENTREV1", "Bank transfer",
                        meta={"bank": "Wema"})
        evidence = ReversalEvidence.objects.create(
            provider=ReversalEvidence.WEMA,
            provider_reference="ALAT-CUSTOMER-STATEMENT-1",
            provider_reference_hash="d" * 64,
            user=self.user, payout=txn, amount=Decimal("500"),
            initial_reason="provider_status_conflict",
            reason="provider_status_conflict", state=ReversalEvidence.ACTIVE,
        )
        ReversalEvidenceObservation.objects.create(
            evidence=evidence, amount=Decimal("500"))

        with patch("utility.providers.send_email",
                   return_value={"success": True}) as send_email:
            response = self._post("/api/wallet/statement/request/", {
                "file_type": "excel", "email": "statement@zitch.test",
            })

        self.assertEqual(response.status_code, 200, response.content)
        attachment = send_email.call_args.kwargs["attachments"][0]
        with zipfile.ZipFile(io.BytesIO(attachment["content"])) as workbook:
            sheet = workbook.read("xl/worksheets/sheet1.xml").decode()
        self.assertIn("under_review", sheet)
        self.assertNotIn(">success<", sheet)
