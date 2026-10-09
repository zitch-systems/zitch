"""Restore both channels while collection liabilities remain isolated."""
import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from utility.providers import partnership_new_business_allowed, _partnership_reference_blocked
from wallet.models import Transaction, Wallet
from wallet.services import (LimitExceeded, biller_source_for_transaction, customer_funding_account,
    debit, settle_or_refund, wallet_balance_payload, wallet_expected_balance)
from wallet.tests import make_user
from wema_vas.models import VirtualAccount
from wema_vas.partnership import return_blockers, return_inventory
from wema_vas.services import process_notification
from wema_vas.tests import LIVE, payload
from whatsapp import router


@override_settings(BANK_ACCOUNT_PROVIDER="partnership", WEMA_PARTNERSHIP_MODE="active",
                   WEMA_PARTNERSHIP_RESTORE_VAS=True, WEMA_VAS=LIVE, WEMA_BILLER_MODE="active")
class PartnershipReturnTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08099990106", "return@example.test", balance="1000", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0459999106"
        self.wallet.account_name = "Ada Eze"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.save()
        self.account = VirtualAccount.objects.create(
            user=self.user, number="9990000106", display_name="Zitch/Ada Eze",
            encrypted_identity="retained", verification_reference="proof-test",
            consent_reference="consent-test", verified_at=timezone.now(), mode="live", prefix="999")

    def response(self, path):
        result = self.client.post(path, json.dumps({"access_token": self.token}), content_type="application/json")
        self.assertEqual(result.status_code, 200)
        return result.json()

    def test_zero_exposure_restores_original_account_in_app_and_whatsapp(self):
        self.assertTrue(partnership_new_business_allowed(self.user))
        for path in ("/api/wallet/account/", "/api/wallet_balance/"):
            state = self.response(path)
            self.assertEqual(state["provider"], "partnership")
            self.assertEqual(state["account_number"], self.wallet.account_number)
            self.assertTrue(state["transfers_available"])
            self.assertTrue(state["bill_payments_available"])
        with patch.object(router, "reply") as reply, patch("whatsapp.vas_flow.start") as vas:
            router._do_add_money(self.user, "2348099990106")
            router._do_account_details(self.user, "2348099990106")
        vas.assert_not_called()
        text = " ".join(call.args[1] for call in reply.call_args_list)
        self.assertIn(self.wallet.account_number, text)
        self.assertNotIn(self.account.number, text)
        self.assertEqual(VirtualAccount.objects.count(), 1)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_zero_exposure_reserves_partnership_bill_source_and_transfer(self):
        bill = debit(self.user, "100", "Airtime — MTN")
        self.assertIsNone(bill.bill_funding.vas_account_id)
        self.assertEqual(biller_source_for_transaction(bill.reference, amount="100"), self.wallet.account_number)
        transfer = debit(self.user, "100", "Transfer to Ada")
        self.assertFalse(_partnership_reference_blocked(transfer.reference, self.wallet.account_number))
        self.assertTrue(_partnership_reference_blocked(transfer.reference, self.account.number))

    def test_late_vas_credit_is_preserved_once_and_closes_both_spend_paths(self):
        before = Transaction.objects.count()
        body = payload(self.account, amount="100")
        self.assertEqual(process_notification(body)[1], 200)
        self.assertEqual(process_notification(body)[1], 200)
        self.assertEqual(Transaction.objects.count(), before + 1)
        self.assertEqual(return_blockers(self.user), ["vas_balance"])
        for service in ("Transfer to Ada", "Airtime — MTN"):
            with self.subTest(service=service), self.assertRaises(LimitExceeded):
                debit(self.user, "1", service)
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1100"))
        self.assertEqual(wallet_balance_payload(self.user)["available_balance"], Decimal("0"))
        state = self.response("/api/wallet/account/")
        self.assertEqual(state["account_setup_state"], "partnership_review")
        self.assertEqual(state["account_number"], "")
        self.assertFalse(state["transfers_available"])
        for handler in (router._do_add_money, router._do_account_details, router._start_add_account, router._start_kyc):
            with patch.object(router, "reply") as reply, patch("whatsapp.vas_flow.start") as vas:
                handler(self.user, "2348099990106")
            vas.assert_not_called()
            self.assertIn("balance review", reply.call_args.args[1])
            self.assertNotIn(self.wallet.account_number, reply.call_args.args[1])

    def test_held_receipt_stays_blocked_even_after_account_unblocked(self):
        self.account.active = False
        self.account.save(update_fields=["active"])
        self.assertEqual(process_notification(payload(self.account))[1], 503)
        self.account.active = True
        self.account.save(update_fields=["active"])
        self.assertEqual(return_blockers(self.user), ["held_receipts"])
        self.assertFalse(partnership_new_business_allowed(self.user))
        self.assertEqual(wallet_expected_balance(self.user.pk), Decimal("1000"))

    def test_pending_collection_bill_with_zero_remainder_cannot_return(self):
        from wallet.test_biller_funding import VAS, COLLECTION
        process_notification(payload(self.account, amount="100"))
        with override_settings(BANK_ACCOUNT_PROVIDER="wema_vas", WEMA_PARTNERSHIP_MODE="archive",
                WEMA_VAS=VAS, WEMA_VAS_BILLER_ENABLED=True,
                WEMA_VAS_BILLER_SOURCE_ACCOUNT=COLLECTION,
                WEMA_VAS_BILLER_APPROVAL_REFERENCE="test-biller-approval"):
            bill = debit(self.user, "100", "Airtime — MTN")
        self.assertEqual(return_blockers(self.user), ["unresolved_bills"])
        self.assertFalse(partnership_new_business_allowed(self.user))
        settle_or_refund(bill, {"success": True})
        self.assertTrue(partnership_new_business_allowed(self.user))

    def test_restore_policy_never_overrides_archive_or_vas_selection(self):
        for values in ({"WEMA_PARTNERSHIP_MODE": "archive"}, {"BANK_ACCOUNT_PROVIDER": "wema_vas"},
                       {"WEMA_PARTNERSHIP_RESTORE_VAS": False}):
            with self.subTest(values=values), override_settings(**values):
                self.assertFalse(partnership_new_business_allowed(self.user))

    def test_inventory_is_aggregate_only_and_does_not_mutate_accounts(self):
        report = return_inventory()
        self.assertEqual(report["eligible_accounts"], 1)
        self.assertEqual(report["review_accounts"], 0)
        self.assertNotIn(self.account.number, json.dumps(report))
        self.assertNotIn(self.wallet.account_number, json.dumps(report))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_validation_account_never_prevents_partnership_return(self):
        other, _ = make_user("08099990107", "validation-return@example.test")
        VirtualAccount.objects.create(user=other, number="7110000107", display_name="Zitch/Test",
            encrypted_identity="retained", verification_reference="proof-fixture", consent_reference="consent-fixture",
            verified_at=timezone.now(), mode="validation", prefix="711", active=False,
            validation_balance=100)
        self.assertTrue(partnership_new_business_allowed(other))
        self.assertEqual(customer_funding_account(other)["provider"], "partnership")
