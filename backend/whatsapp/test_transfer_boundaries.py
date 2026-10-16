"""WhatsApp payment validation must match the live bank/account guardrails."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from transfers.models import Bank
from wallet.models import Transaction
from whatsapp import flows, router
from whatsapp.models import PendingAction
from whatsapp.test_flows import MSISDN, _make_user, _transfer_action


class TransferBoundaryTests(TestCase):
    def setUp(self):
        self.user = _make_user()
        self.bank, _ = Bank.objects.get_or_create(code="gtb", defaults={
            "name": "GTBank", "bank_code": "058", "active": True})

    def test_stale_bank_picker_does_not_resolve_a_disabled_bank(self):
        pa = _transfer_action(self.user)
        pa.state = "bank_pick"
        pa.payload["bank_choices"] = [self.bank.code]
        pa.save(update_fields=["state", "payload"])
        self.bank.active = False
        self.bank.save(update_fields=["active"])
        with patch.object(router, "payout_resolve_account") as enquiry:
            router._advance_transfer(pa, self.user, MSISDN, "1")
        enquiry.assert_not_called()

    def test_new_payment_to_disabled_bank_never_calls_bank_or_executor(self):
        pa = _transfer_action(self.user)
        self.bank.active = False
        self.bank.save(update_fields=["active"])
        with patch.object(router, "payout_resolve_account") as enquiry, \
                patch.object(router, "execute_payout") as payout:
            result = router._exec_transfer(pa, self.user, MSISDN)
        self.assertEqual(result.status, router.OUTCOME_FAILED)
        enquiry.assert_not_called()
        payout.assert_not_called()

    def test_failed_or_empty_name_enquiry_never_starts_payment(self):
        for response in ({"success": False}, {"success": True, "name": ""}):
            with self.subTest(response=response):
                pa = _transfer_action(self.user)
                with patch.object(router, "payout_resolve_account", return_value=response), \
                        patch.object(router, "execute_payout") as payout:
                    result = router._exec_transfer(pa, self.user, MSISDN)
                self.assertEqual(result.status, router.OUTCOME_FAILED)
                payout.assert_not_called()
                self.assertFalse(Transaction.objects.filter(idempotency_key=f"wa-{pa.pk}").exists())

    def test_existing_payment_replays_before_bank_and_velocity_checks(self):
        self.bank.active = False
        self.bank.save(update_fields=["active"])
        for status, outcome in ((Transaction.SUCCESS, router.OUTCOME_SUCCESS),
                                (Transaction.PENDING, router.OUTCOME_PENDING),
                                (Transaction.FAILED, router.OUTCOME_FAILED)):
            with self.subTest(status=status):
                pa = _transfer_action(self.user)
                Transaction.objects.create(user=self.user, amount=Decimal("5000"),
                    service="Transfer", direction=Transaction.OUT, transaction_status=status,
                    reference=f"WA-existing-{pa.pk}", idempotency_key=f"wa-{pa.pk}")
                with patch.object(router, "payout_resolve_account") as enquiry, \
                        patch.object(router, "execute_payout") as payout, \
                        patch.object(router, "velocity_exceeded", return_value=True) as velocity, \
                        patch.object(router, "reply_receipt"):
                    result = router._exec_transfer(pa, self.user, MSISDN)
                self.assertEqual(result.status, outcome)
                enquiry.assert_not_called()
                payout.assert_not_called()
                velocity.assert_not_called()

    def test_secure_form_rejects_nonfinite_amount_and_malformed_account(self):
        pa = PendingAction.objects.create(user=self.user, msisdn=MSISDN,
            action_type="transfer", state=flows.FLOW_FORM_STATE, payload={},
            expires_at=timezone.now() + timedelta(minutes=5))
        cases = [{"amount": raw, "account_number": "0123456789"}
                 for raw in ("NaN", "Infinity", "1e500", None)]
        cases += [{"amount": "5000", "account_number": raw}
                  for raw in ("abc0123456789", "٠١٢٣٤٥٦٧٨٩", ["0123456789"])]
        for data in cases:
            with self.subTest(data=data), patch("utility.providers.payout_resolve_account") as enquiry:
                response = flows._submit_transfer_form(flows.sign_flow_token(pa), {"bank": "gtb", **data})
                self.assertEqual(response["screen"], flows.TRANSFER_FORM)
                self.assertTrue(response["data"]["error"])
                enquiry.assert_not_called()
