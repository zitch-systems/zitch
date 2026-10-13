"""Retirement removes operations while preserving money, evidence and holds."""
import importlib.util
import json
from io import StringIO
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command, get_commands
from django.db import IntegrityError, connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase, override_settings, skipUnlessDBFeature
from django.urls import Resolver404, resolve

from legacy_funding.models import MigrationApproval, Receipt, VirtualAccount
from wallet.legacy_funding import account_balance
from wallet.legacy_test_fixtures import account_fixture, retained_receipt
from wallet.models import BillFundingBinding, Transaction, Wallet
from wallet.partnership import legacy_funding_blockers
from wallet.services import LimitExceeded, customer_funding_account, debit, wallet_balance_payload, wema_provisioned_wallets


@override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership", WEMA_BILLER_MODE="active")
class RetiredFundingTests(TestCase):
    def setUp(self):
        self.user, self.account = account_fixture()
        self.wallet = Wallet.objects.get(user=self.user)
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0459990001", account_reference="WEMA-WALLET-fixture")

    def test_removed_routes_commands_and_provider_package_are_absent(self):
        for path in ("/vas/account-lookup/", "/vas/transaction-notification/", "/vas/mini-statement/",
                     "/vas/kyc-details/", "/vas/block-account/", "/api/wallet/vas/enroll/",
                     "/api/wallet/vas/status/", "/api/wallet/vas/identity/start/",
                     "/api/wallet/vas/identity/confirm/", "/api/wallet/vas/identity/resend/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
        self.assertFalse(any(name.startswith("vas_") for name in get_commands()))
        self.assertIsNone(importlib.util.find_spec("wema_vas"))
        self.assertFalse(Transaction.objects.exists())

    def test_funded_record_is_visible_only_as_held_historical_money(self):
        receipt = retained_receipt(self.account, "1250")
        before = list(Transaction.objects.values("id", "amount", "transaction_status", "meta"))
        state = customer_funding_account(self.user)
        self.assertEqual(state["provider"], "partnership")
        self.assertEqual(state["account_setup_state"], "legacy_funding_review")
        self.assertTrue(state["legacy_funding_review"])
        for flag in ("has_account", "available", "can_fund", "spending_available", "transfers_available", "bill_payments_available"):
            self.assertFalse(state[flag])
        for number in (self.account.number, "0459990001"):
            self.assertNotIn(number, json.dumps(state))
        self.assertNotIn("enrollment_available", state)
        self.assertEqual(wallet_balance_payload(self.user), {"balance": Decimal("1250"),
            "available_balance": Decimal("0"), "historical_balance": Decimal("1250"),
            "legacy_funding_balance": Decimal("1250")})
        self.assertEqual(list(Transaction.objects.values("id", "amount", "transaction_status", "meta")), before)
        self.assertEqual(Receipt.objects.get(pk=receipt.pk).transaction_id, receipt.transaction_id)

    def test_retired_number_in_wallet_is_never_partnership_even_with_zero_balance(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number=self.account.number)
        state = customer_funding_account(self.user)
        self.assertFalse(state["can_fund"])
        self.assertEqual(state["account_number"], "")
        self.assertTrue(state["legacy_funding_review"])
        self.assertNotIn(self.wallet.pk, wema_provisioned_wallets().values_list("pk", flat=True))
        with self.assertRaises(LimitExceeded):
            debit(self.user, "1", "Transfer to Ada")

    def test_retired_number_cannot_reach_partnership_history_or_credit(self):
        from accounts.models import AccessToken
        from wallet.models import BankHistoryCheckpoint
        from wallet.reconciliation import reconcile_account_history
        from wallet.services import apply_wema_credit
        from wallet.management.commands.reconciliation_evidence import build_evidence, pool_evidence
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number=self.account.number)
        self.wallet.refresh_from_db()
        token = AccessToken.issue(self.user).key
        with patch("utility.wema.get_transactions") as history, patch("utility.wema.get_balance") as balance:
            result = reconcile_account_history(self.wallet)
            report = build_evidence(self.wallet)
            response = self.client.post("/api/wallet/statement/", {"access_token": token}, content_type="application/json")
            with override_settings(WEMA={"SOURCE_ACCOUNT": self.account.number}), patch("utility.wema.wema_live", return_value=True):
                pool = pool_evidence()
            applied = apply_wema_credit(self.wallet, {"referenceId": "retired-row", "amount": "100",
                "creditType": "Credit", "status": "Successfull"})
        history.assert_not_called()
        balance.assert_not_called()
        self.assertEqual(result["error_code"], "retired_funding_account")
        self.assertEqual(report["status"], "unrecognized_account_provenance")
        self.assertEqual(pool["status"], "invalid_partnership_source")
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(applied)
        self.assertFalse(BankHistoryCheckpoint.objects.exists())
        self.assertFalse(Transaction.objects.exists())

    @override_settings(ZITCH_LINKS={"API_BASE": "https://testserver"})
    def test_shared_guard_blocks_upgrades_and_verification_for_populated_retired_numbers(self):
        from utility.providers import partnership_new_business_allowed
        from wallet.partnership import partnership_allowed, legacy_funding_inventory
        from wallet.views import upgrade_wema_identity
        from whatsapp.models import PendingAction, WhatsAppLink
        from whatsapp.verification_web import start_verification
        from django.utils import timezone
        msisdn = "2348099900001"
        WhatsAppLink.objects.create(user=self.user, wa_msisdn=msisdn,
            status=WhatsAppLink.ACTIVE, linked_at=timezone.now())
        for number in (self.account.number, "7110000099", "invalid"):
            with self.subTest(number=number), patch("utility.wema.upgrade_tier2") as upgrade:
                Wallet.objects.filter(pk=self.wallet.pk).update(account_number=number)
                self.assertEqual(legacy_funding_blockers(self.user), [])
                self.assertFalse(partnership_allowed(self.user))
                self.assertFalse(partnership_new_business_allowed(self.user))
                self.assertEqual(upgrade_wema_identity(self.user, {}).status_code, 409)
                with self.assertRaises(ValueError):
                    start_verification(self.user, msisdn, 2)
                upgrade.assert_not_called()
                self.assertEqual(legacy_funding_inventory()["review_accounts"], 1)
        self.assertFalse(PendingAction.objects.exists())
        Wallet.objects.filter(pk=self.wallet.pk).update(account_number="")
        self.assertTrue(partnership_allowed(self.user))
        self.assertTrue(partnership_new_business_allowed(self.user))
        self.assertEqual(legacy_funding_inventory()["eligible_accounts"], 1)

    def test_invalid_or_validation_number_never_exposes_bank_details(self):
        for number in ("7111234567", "bad-number"):
            with self.subTest(number=number):
                Wallet.objects.filter(pk=self.wallet.pk).update(account_number=number)
                state = customer_funding_account(self.user)
                self.assertFalse(state["can_fund"])
                self.assertEqual(state["bank_accounts"], [])
                self.assertEqual(state["account_number"], "")

    def test_validation_records_do_not_change_partnership_money(self):
        other, account = account_fixture(suffix="2", mode="validation")
        account.validation_balance = Decimal("9000")
        account.save(update_fields=["validation_balance"])
        Wallet.objects.filter(user=other).update(account_number="0459990002")
        self.assertEqual(legacy_funding_blockers(other), [])
        state = customer_funding_account(other)
        self.assertTrue(state["can_fund"])
        self.assertEqual(state["account_number"], "0459990002")
        self.assertEqual(wallet_balance_payload(other)["legacy_funding_balance"], 0)
        self.assertEqual(Wallet.objects.get(user=other).balance, 0)

    def test_archived_package_keeps_original_migration_identity_and_schema(self):
        self.assertEqual(apps.get_app_config("wema_vas").name, "legacy_funding")
        self.assertEqual(VirtualAccount._meta.db_table, "wema_vas_virtualaccount")
        self.assertEqual(Receipt._meta.db_table, "wema_vas_receipt")
        self.assertEqual(BillFundingBinding._meta.get_field("vas_account").remote_field.model, VirtualAccount)
        self.assertIn(("wema_vas", "0004_separate_account_modes"), MigrationLoader(connection).graph.leaf_nodes())

    def test_historical_liabilities_keep_bank_comparison_incomplete(self):
        from whatsapp.models import AuditLog
        retained_receipt(self.account, "1000")
        out, err = StringIO(), StringIO()
        with patch("utility.wema.wema_live", return_value=True), patch("utility.wema.get_balance") as bank, patch("utility.alerts.alert"):
            with self.assertRaises(SystemExit):
                call_command("reconcile_balances", "--fail-nonzero", stdout=out, stderr=err)
        bank.assert_not_called()
        self.assertIn("INCOMPLETE", out.getvalue())
        self.assertNotIn("OVER", err.getvalue())
        evidence = AuditLog.objects.get(action="recon.balance_check").after
        self.assertTrue(evidence["legacy_funding_required"])
        self.assertEqual(evidence["ledger_over_bank"], 0)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000"))

    def test_historical_assets_are_not_invented_from_pool_or_nuban(self):
        from whatsapp.models import AuditLog
        retained_receipt(self.account, "1000")
        with patch("utility.wema.wema_live", return_value=True), patch("utility.wema.get_balance", return_value={
                "success": True, "balance_naira": Decimal("0")}), patch("utility.alerts.alert"):
            with self.assertRaises(SystemExit):
                call_command("settlement_report", "--fail-on-breach", stdout=StringIO())
        evidence = AuditLog.objects.get(action="recon.settlement_report").after
        self.assertIsNone(evidence["position"])
        self.assertIsNone(evidence["held_total"])
        self.assertTrue(evidence["incomplete"])
        self.assertFalse(evidence["legacy_funding_verified"])

    def test_unverified_historical_bank_evidence_fails_without_partnership_connection(self):
        with patch("utility.wema.wema_live", return_value=False), patch("utility.alerts.alert"):
            for name, flag in (("settlement_report", "--fail-on-breach"), ("reconcile_balances", "--fail-nonzero")):
                with self.subTest(command=name), self.assertRaises(SystemExit):
                    call_command(name, flag, stdout=StringIO(), stderr=StringIO())

    def test_historical_identity_and_receipts_are_immutable(self):
        receipt = retained_receipt(self.account)
        receipt.amount = Decimal("1")
        with self.assertRaises(ValidationError):
            receipt.save()
        self.account.number = "9990000099"
        with self.assertRaises(ValidationError):
            self.account.save()
        self.assertEqual(account_balance(self.account), Decimal("1000"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_postgres_evidence_guards_survive_package_rename(self):
        receipt = retained_receipt(self.account)
        approval = MigrationApproval.objects.create(user=self.user, reference="historical-evidence",
            approved_by="historical-operator", legacy_account_number="0459990001")
        for model, changes in ((Receipt, {"amount": Decimal("1")}), (MigrationApproval, {"reference": "changed"}),
                               (VirtualAccount, {"number": "9990000099"})):
            for action in (lambda: model.objects.update(**changes), lambda: model.objects.all().delete()):
                with self.subTest(model=model.__name__), self.assertRaises(IntegrityError), transaction.atomic():
                    action()
        self.assertTrue(Receipt.objects.filter(pk=receipt.pk).exists())
        self.assertTrue(MigrationApproval.objects.filter(pk=approval.pk).exists())
