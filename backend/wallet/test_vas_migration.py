"""Migration boundaries protect money and preserve the legacy settlement path."""
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.views import _kyc_state
from transfers.services import PayoutError, execute_payout
from utility import providers
from wallet.models import Transaction, Wallet
from wallet.services import (
    LimitExceeded, apply_wema_credit, customer_funding_account, debit,
    run_provider_purchase, settle_or_refund, wema_provisioned_wallets,
)
from wallet.tests import make_user
from wallet.views import _start_wema_attempt


@override_settings(WEMA_PARTNERSHIP_MODE="archive", BANK_ACCOUNT_PROVIDER="wema_vas")
class PartnershipArchiveBoundaryTests(TestCase):
    def setUp(self):
        self.user, self.token = make_user("08099990101", "vas-archive@example.com", balance="1000")
        self.wallet = Wallet.objects.get(user=self.user)
        self.wallet.account_number = "0459999101"
        self.wallet.account_name = "Ada Eze"
        self.wallet.account_reference = f"WEMA-WALLET-{self.user.pk}"
        self.wallet.bank_name = "Wema Bank"
        self.wallet.bank_tier = 1
        self.wallet.save()

    def post(self, path, **data):
        return self.client.post(path, data=json.dumps({"access_token": self.token, **data}),
                                content_type="application/json")

    def test_archive_refuses_new_account_and_upgrade_without_provider_calls(self):
        with patch("utility.wema.create_wallet_request") as create, \
                patch("utility.wema.upgrade_tier2") as upgrade, \
                patch("utility.wema.face_verification_url") as face:
            self.assertEqual(self.post("/api/wallet/account/create/", bvn="12345678901").status_code, 409)
            self.assertEqual(self.post("/api/wallet/wema/create/", bvn="12345678901").status_code, 409)
            self.assertEqual(self.post("/api/wallet/wema/upgrade-tier2/").status_code, 409)
            self.assertEqual(self.post("/api/kyc/face/start/", bvn="12345678901").status_code, 409)
            result, error = _start_wema_attempt(self.user, "12345678901", "")
            self.assertIsNone(result)
            self.assertTrue(error)
        create.assert_not_called()
        upgrade.assert_not_called()
        face.assert_not_called()

    def test_archive_preserves_existing_biller_debit_and_provider_call(self):
        count = Transaction.objects.count()
        with patch("utility.wema.purchase_airtime", return_value={"success": True}) as purchase:
            status, txn, _ = run_provider_purchase(self.user, "100", "Airtime · MTN", {}, purchase)
        self.assertEqual(status, "success")
        purchase.assert_called_once_with(txn.reference)
        self.assertEqual(txn.bill_funding.source_account, self.wallet.account_number)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("900"))
        self.assertEqual(Transaction.objects.count(), count + 1)

    def test_payout_stops_before_legacy_debit_activation(self):
        bank = SimpleNamespace(name="Bank", bank_code="000013")
        with patch("utility.wema.lift_debit_restriction") as pnd, \
                patch("transfers.services.payout_send") as send:
            with self.assertRaises(PayoutError):
                execute_payout(self.user, Decimal("100"), "0123456789", bank, "Other Person")
        pnd.assert_not_called()
        send.assert_not_called()
        self.wallet.refresh_from_db()
        self.assertFalse(self.wallet.pnd_lifted)
        self.assertEqual(self.wallet.balance, Decimal("1000"))

    def test_wrappers_never_fall_back_to_old_pool(self):
        with patch("utility.wema.transfer") as send, patch("utility.wema.purchase_airtime") as airtime:
            result = providers.payout_send("100", "old-ref", "Test", "000013", "0123456789", "Other")
            self.assertFalse(result["success"])
            self.assertTrue(result["not_charged"])
            result = providers.vtu_purchase("mtn-airtime", {"amount": "100", "phone": self.user.phone})
            self.assertFalse(result["success"])
        send.assert_not_called()
        airtime.assert_not_called()

    def test_archived_number_is_preserved_but_never_advertised_for_new_funding(self):
        self.assertEqual(customer_funding_account(self.user)["account_number"], "")
        for path in ("/api/wallet/account/", "/api/wallet_balance/"):
            response = self.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["account_number"], "")
            self.assertFalse(response.json()["spending_available"])
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.account_number, "0459999101")
        self.assertIn(self.wallet, list(wema_provisioned_wallets()))

    def test_statement_does_not_query_archived_account_as_current(self):
        with patch("utility.wema.get_transactions") as query:
            response = self.post("/api/wallet/statement/")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "wallet_statement_required")
        query.assert_not_called()

    def test_kyc_does_not_offer_partnership_upgrades(self):
        result = _kyc_state(self.user)
        self.assertFalse(result["bank_upgrade_required"])
        self.assertFalse(result["identity_face_available"])
        self.assertEqual(result["bank_tier"], 0)
        self.assertEqual(result["identity_verification_methods"], ["sms_otp"])

    def test_late_legacy_credit_is_still_applied_once(self):
        incoming = {"referenceId": "legacy-late-credit", "amount": "250", "creditType": "Credit",
                    "status": "Successfull", "date": timezone.now().isoformat(),
                    "narration": "Late deposit", "sender": "Customer"}
        apply_wema_credit(self.wallet, incoming)
        apply_wema_credit(self.wallet, incoming)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1250"))
        self.assertEqual(Transaction.objects.filter(user=self.user, amount="250").count(), 1)

    def test_prior_pending_debit_can_still_refund(self):
        with override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership"):
            pending = debit(self.user, "100", "Airtime · MTN")
        self.assertEqual(settle_or_refund(pending, {"success": False, "message": "Rejected"}), "failed")
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000"))

    def test_identity_failure_has_no_legacy_bank_fallback(self):
        with patch("utility.providers._prembly_identity_live", return_value=False), \
                patch("utility.wema.verify_bvn") as verify:
            result = providers.verify_bvn("12345678901", name="Ada Eze")
        self.assertFalse(result["success"])
        self.assertFalse(result.get("otp_required", False))
        verify.assert_not_called()


class ProviderSelectionBoundaryTests(TestCase):
    @override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="wema_vas")
    def test_selecting_vas_does_not_silently_select_partnership(self):
        self.assertEqual(providers.payment_provider(), "wema_vas")
        self.assertEqual(providers.payout_provider(), "unavailable")
        self.assertFalse(providers.partnership_new_business_allowed())

    @override_settings(WEMA_PARTNERSHIP_MODE="typo", BANK_ACCOUNT_PROVIDER="partnership")
    def test_unknown_lifecycle_fails_closed(self):
        self.assertFalse(providers.partnership_new_business_allowed())


@override_settings(WEMA_PARTNERSHIP_MODE="active", BANK_ACCOUNT_PROVIDER="partnership")
class PerCustomerMigrationBoundaryTests(TestCase):
    def setUp(self):
        from wema_vas.models import VirtualAccount

        self.user, self.token = make_user("08099990102", "vas-customer@example.com", balance="1000")
        self.account = VirtualAccount.objects.create(
            user=self.user, number="7120000102", display_name="Zitch/Ada Eze",
            encrypted_identity="encrypted-test-only", verification_reference="proof-test-only",
            consent_reference="consent-test-only", verified_at=timezone.now(),
            mode="live", prefix="712")

    def test_migrated_customer_cannot_open_partnership_even_before_global_archive(self):
        with patch("utility.wema.create_wallet_request") as create:
            result, error = _start_wema_attempt(self.user, "12345678901", "")
        self.assertIsNone(result)
        self.assertTrue(error)
        create.assert_not_called()

    def test_migrated_customer_cannot_spend_before_global_archive(self):
        with self.assertRaises(LimitExceeded):
            debit(self.user, "100", "Airtime · MTN")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000"))

    def test_migrated_customer_source_cannot_reach_partnership_biller(self):
        with patch("utility.wema.purchase_airtime") as purchase:
            result = providers.vtu_purchase("mtn-airtime", {
                "amount": "100", "phone": self.user.phone, "source_account": self.account.number})
        self.assertFalse(result["success"])
        purchase.assert_not_called()

    def test_migrated_customer_legacy_nuban_cannot_reach_biller_without_a_reference(self):
        Wallet.objects.filter(user=self.user).update(account_number="0459999102")
        with patch("utility.wema.purchase_airtime") as purchase:
            result = providers.vtu_purchase("mtn-airtime", {
                "amount": "100", "phone": self.user.phone, "source_account": "0459999102"})
        self.assertFalse(result["success"])
        purchase.assert_not_called()


class VASFinancialReportBoundaryTests(PerCustomerMigrationBoundaryTests):
    def test_migrated_balance_never_compared_against_old_nuban(self):
        from django.core.management import call_command
        from io import StringIO
        from whatsapp.models import AuditLog

        Wallet.objects.filter(user=self.user).update(
            account_number="0459999102", account_reference=f"WEMA-WALLET-{self.user.pk}")
        out, err = StringIO(), StringIO()
        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance") as bank, patch("utility.alerts.alert"):
            with self.assertRaises(SystemExit):
                call_command("reconcile_balances", "--fail-nonzero", stdout=out, stderr=err)
        bank.assert_not_called()
        self.assertIn("INCOMPLETE", out.getvalue())
        self.assertNotIn("OVER", err.getvalue())
        audit = AuditLog.objects.get(action="recon.balance_check")
        self.assertTrue(audit.after["vas_collection_required"])
        self.assertEqual(audit.after["ledger_over_bank"], 0)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("1000"))

    def test_vas_collection_not_replaced_by_legacy_pool_balance(self):
        from django.core.management import call_command
        from io import StringIO
        from whatsapp.models import AuditLog

        out = StringIO()
        with patch("utility.wema.wema_live", return_value=True), \
                patch("utility.wema.get_balance", return_value={"success": True, "balance_naira": Decimal("0")}), \
                patch("utility.alerts.alert") as alerts:
            with self.assertRaises(SystemExit):
                call_command("settlement_report", "--fail-on-breach", stdout=out)
        self.assertIn("POSITION UNAVAILABLE", out.getvalue())
        self.assertNotIn("SHORTFALL", out.getvalue())
        self.assertFalse(any("SHORTFALL" in call.args[0] for call in alerts.call_args_list))
        audit = AuditLog.objects.get(action="recon.settlement_report")
        self.assertIsNone(audit.after["position"])
        self.assertIsNone(audit.after["held_total"])
        self.assertTrue(audit.after["incomplete"])
        self.assertFalse(audit.after["vas_collection_verified"])

    def test_vas_unverified_report_fails_even_without_live_partnership_keys(self):
        from django.core.management import call_command
        from io import StringIO

        with patch("utility.wema.wema_live", return_value=False), patch("utility.alerts.alert"):
            for name, flag in (("settlement_report", "--fail-on-breach"),
                               ("reconcile_balances", "--fail-nonzero")):
                with self.subTest(command=name), self.assertRaises(SystemExit):
                    call_command(name, flag, stdout=StringIO(), stderr=StringIO())
