"""Regression tests for duplicate WhatsApp money submissions.

An idempotency collision says only that an earlier request exists.  The customer
message must come from that request's ledger state; treating every duplicate as
"already processed" either claims an unsettled payment succeeded or calls a
failed/refunded payment successful.
"""
from banking.test_fixtures import bank_channel_case
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from transfers.services import PayoutError
from wallet.models import Transaction
from wallet.services import DuplicateTransaction, get_or_create_wallet

from . import router
from .flows import FLOW_PIN_STATE
from .models import PendingAction


User = get_user_model()
MSISDN = "2348099991111"


@bank_channel_case
class DuplicateMoneyOutcomeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="08099991111", phone="08099991111",
            email="replay@zitch.test", first_name="Replay", last_name="Tester",
            tier=1, email_verified=True, phone_verified=True, bvn_verified=True,
        )
        get_or_create_wallet(self.user)
        self.bank = Bank.objects.create(
            code="gtb-replay", name="GTBank", bank_code="058", active=True,
        )

    def _transfer_action(self):
        return PendingAction.objects.create(
            user=self.user, msisdn=MSISDN, action_type="transfer", state=FLOW_PIN_STATE,
            payload={
                "amount": "5000", "account": "0123456789", "bank_code": "058",
                "bank_name": "GTBank", "name": "JOHN DOE", "pin_attempts": 0,
            },
            expires_at=timezone.now() + timedelta(minutes=5),
        )

    def _ledger(self, pa, status, *, service="Transfer", meta=None):
        return Transaction.objects.create(
            user=self.user, service=service, amount=Decimal(pa.payload["amount"]),
            direction=Transaction.OUT, transaction_status=status,
            reference=f"ZWAREPLAY{pa.pk:08d}", idempotency_key=f"wa-{pa.pk}",
            meta=meta or {},
        )

    def test_transfer_duplicate_reports_each_durable_state_without_repaying(self):
        cases = (
            (Transaction.SUCCESS, router.OUTCOME_SUCCESS, "already completed", True),
            (Transaction.PENDING, router.OUTCOME_PENDING, "still processing", False),
            (Transaction.FAILED, router.OUTCOME_FAILED, "failed", False),
        )
        for index, (ledger_status, outcome_status, phrase, has_receipt) in enumerate(cases):
            with self.subTest(status=ledger_status):
                pa = self._transfer_action()
                pa.msisdn = f"{MSISDN[:-1]}{index}"
                pa.save(update_fields=["msisdn"])
                self._ledger(pa, ledger_status, meta={"failure": "Bank declined it"})
                with patch.object(
                    router, "payout_resolve_account", return_value={"success": False}
                ), patch.object(
                    router, "execute_payout",
                    side_effect=PayoutError("duplicate", "Already submitted"),
                ) as execute, patch.object(router, "reply_receipt") as receipt:
                    outcome = router._exec_transfer(pa, self.user, pa.msisdn)

                self.assertEqual(outcome.status, outcome_status)
                self.assertIn(phrase, outcome.lower())
                self.assertEqual(receipt.called, has_receipt)
                execute.assert_not_called()
                self.assertEqual(
                    Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").count(), 1)

    def test_duplicate_without_a_visible_ledger_row_is_not_called_success(self):
        """A duplicate the ledger cannot show must never be reported as paid.

        execute_payout raising "duplicate" says only that this action's key was
        used before. If no row is readable under it, the outcome is genuinely
        unknown: the customer is told not to send it again and to contact
        support, and the message must not claim the money arrived.

        This property was covered on the airtime path, which is gone with the
        VAS rail. _exec_transfer has the same branch, so the test moved here
        rather than being deleted with the rail -- the risk it guards (telling
        someone a payment succeeded when nothing proves it did) is the same.
        """
        pa = self._transfer_action()
        # The enquiry has to SUCCEED here. _exec_transfer re-resolves the
        # account immediately before paying, so a failed enquiry returns
        # "couldn't verify the recipient" and the duplicate branch under test is
        # never reached -- the test would pass or fail on the wrong code. mock=True
        # is what skips the name-change comparison, as it does in the sandbox.
        with patch.object(
            router, "payout_resolve_account",
            return_value={"success": True, "name": "JOHN DOE", "mock": True},
        ), patch.object(
            router, "execute_payout",
            side_effect=PayoutError("duplicate", "Already submitted"),
        ) as execute, patch.object(router, "reply_receipt") as receipt:
            outcome = router._exec_transfer(pa, self.user, pa.msisdn)

        self.assertEqual(outcome.status, router.OUTCOME_PENDING)
        self.assertIn("status is not available", outcome)
        self.assertIn("do not send it again", outcome.lower())
        execute.assert_called_once()
        receipt.assert_not_called()
        self.assertFalse(
            Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").exists())
