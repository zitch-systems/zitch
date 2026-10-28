"""Outstanding money survives removal without enabling a retired provider."""
import importlib.util
import json
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command, get_commands
from django.db import connection, IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import Resolver404, resolve

from wallet.funding_test_fixtures import funding_hold
from wallet.models import Transaction, UnsettledFunding, Wallet
from wallet.partnership import legacy_funding_blockers, partnership_account_number
from wallet.services import LimitExceeded, customer_funding_account, debit, wallet_balance_payload, wema_provisioned_wallets
from wallet.tests import make_user


@override_settings(WEMA_PARTNERSHIP_MODE="active", WEMA_BILLER_MODE="active")
class FundingHoldTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08099900001", "funding-hold@zitch.test", tier=3)
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0459990001"
        self.wallet.account_reference = "WEMA-WALLET-fixture"
        self.wallet.save()

    def test_removed_routes_commands_and_provider_package_are_absent(self):
        for path in ("/vas/account-lookup/", "/vas/transaction-notification/", "/vas/mini-statement/",
                     "/vas/kyc-details/", "/vas/block-account/", "/api/wallet/vas/enroll/",
                     "/api/wallet/vas/status/", "/api/wallet/vas/identity/start/",
                     "/api/wallet/vas/identity/confirm/", "/api/wallet/vas/identity/resend/"):
            with self.subTest(path=path), self.assertRaises(Resolver404):
                resolve(path)
        self.assertFalse(any(name.startswith("vas_") for name in get_commands()))
        self.assertNotIn("purge_retired_accounts", get_commands())
        self.assertIsNone(importlib.util.find_spec("wema_vas"))

    def test_outstanding_money_blocks_funding_and_both_spend_paths(self):
        funding_hold(self.user, "1250")
        before = list(Transaction.objects.values())
        state = customer_funding_account(self.user)
        self.assertEqual(state["provider"], "partnership")
        self.assertEqual(state["account_setup_state"], "legacy_funding_review")
        for flag in ("has_account", "available", "can_fund", "spending_available", "transfers_available", "bill_payments_available"):
            self.assertFalse(state[flag])
        self.assertNotIn(self.wallet.account_number, json.dumps(state))
        self.assertEqual(wallet_balance_payload(self.user), {"balance": Decimal("1250"),
            "available_balance": Decimal("0"), "historical_balance": Decimal("1250"),
            "legacy_funding_balance": Decimal("1250")})
        for service in ("Airtime — MTN", "Transfer to Ada"):
            with self.assertRaises(LimitExceeded):
                debit(self.user, "1", service)
        self.assertEqual(list(Transaction.objects.values()), before)

    def test_deleted_destination_cannot_be_reused_or_polled(self):
        funding_hold(self.user, "100", self.wallet.account_number)
        self.assertFalse(partnership_account_number(self.wallet.account_number))
        self.assertNotIn(self.wallet.pk, wema_provisioned_wallets().values_list("pk", flat=True))
        from wallet.reconciliation import reconcile_account_history
        from wallet.services import apply_wema_credit
        with patch("utility.wema.get_transactions") as history:
            self.assertEqual(reconcile_account_history(self.wallet)["error_code"], "retired_funding_account")
            self.assertIsNone(apply_wema_credit(self.wallet, {}))
        history.assert_not_called()

    def test_invalid_or_validation_number_never_exposes_bank_details(self):
        for number in ("7111234567", "bad-number"):
            with self.subTest(number=number):
                Wallet.objects.filter(pk=self.wallet.pk).update(account_number=number)
                state = customer_funding_account(self.user)
                self.assertFalse(state["can_fund"])
                self.assertEqual(state["bank_accounts"], [])
                self.assertEqual(state["account_number"], "")

    def test_historical_bank_assets_are_never_invented(self):
        from whatsapp.models import AuditLog
        funding_hold(self.user, "1000")
        with patch("utility.wema.wema_live", return_value=True), patch("utility.wema.get_balance", return_value={
                "success": True, "balance_naira": Decimal("0")}), patch("utility.alerts.alert"):
            for name, flag in (("settlement_report", "--fail-on-breach"), ("reconcile_balances", "--fail-nonzero")):
                with self.subTest(command=name), self.assertRaises(SystemExit):
                    call_command(name, flag, stdout=StringIO(), stderr=StringIO())
        evidence = AuditLog.objects.get(action="recon.settlement_report").after
        self.assertIsNone(evidence["position"])
        self.assertTrue(evidence["incomplete"])
        self.assertFalse(evidence["legacy_funding_verified"])
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000"))

    def test_hold_evidence_is_immutable(self):
        hold = funding_hold(self.user, "100")
        self.assertEqual(legacy_funding_blockers(self.user), ["legacy_balance"])
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL evidence guard")
        for action in (lambda: UnsettledFunding.objects.all().delete(),
                       lambda: UnsettledFunding.objects.update(amount="1")):
            with self.assertRaises(IntegrityError), transaction.atomic():
                action()
        self.assertTrue(UnsettledFunding.objects.filter(pk=hold.pk, amount="100").exists())
