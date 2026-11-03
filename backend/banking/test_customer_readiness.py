"""Customer refresh recovers availability without inventing bank or identity evidence."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AccessToken, User, hash_identifier, record_identity_proof
from wallet.models import Wallet
from wallet.services import credit
from .accounts import account_payload, update_account
from .address import start, verification_status
from .client import ProviderError, Reply
from .models import AccountUpgrade, AddressVerification, BankAccount, PreviousBankArchive
from .secrets import seal, unseal
from .test_client import CONFIG
from .test_upgrade_rules import profile

PHONE = "08010000001"
SANDBOX = {**CONFIG, "ENVIRONMENT": "sandbox", "SANDBOX_CUSTOMERS_ENABLED": True,
           "SANDBOX_PHONES": [PHONE], "SANDBOX_AUTO_VERIFY": True, "TRANSFER_FEE_TOTAL": ""}
ADDRESS = {"house_number": "12", "street": "Test Road", "address": "12 Test Road, Ikeja, Lagos",
           "city": "Ikeja", "state": "Lagos", "lga": "Ikeja", "landmark": "School"}


@override_settings(NINEPSB=SANDBOX)
class CustomerReadinessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="readiness", first_name="Jane", last_name="Doe",
            phone=PHONE, email="jane@example.test", email_verified=True, phone_verified=True,
            bvn_hash=hash_identifier("12345678901"), bvn_verified=True, tier=1)
        self.wallet = Wallet.objects.create(user=self.user)
        self.bank = BankAccount.objects.create(wallet=self.wallet, number="1100000001", name="ZITCH/Jane Doe",
            encrypted_bvn=seal("12345678901"), environment="sandbox", tier=1, pnd=False, frozen=False,
            status="active", checked_at=timezone.now()-timedelta(minutes=20), available_balance=0,
            ledger_balance=0, opening_review_required=False)
        record_identity_proof(self.user, "bvn", "12345678901", source="9psb_otp")
        credit(self.user, Decimal("150150.00"), "Sandbox balance reconciliation", reference="ZSR-READINESS",
            meta={"provider": "9psb", "environment": "sandbox", "account_number": self.bank.number,
                  "sandbox_balance_reconciliation": "ZSR-READINESS", "evidence": "bank_balance_enquiry"})
        self.token = AccessToken.issue(self.user).key

    def reply(self, tier=1, **changes):
        return Reply({"status": "SUCCESS", "data": {"nuban": self.bank.number, "name": self.bank.name,
            "status": "Active", "tier": str(tier), "pndstatus": "InActive", "freezeStatus": "InActive",
            "availableBalance": "150150.00", "ledgerBalance": "150150.00", **changes}}, 200)

    def post(self, path, **body):
        return self.client.post(path, data={"access_token": self.token, **body}, content_type="application/json")

    def test_refreshing_wallet_updates_stale_bank_evidence_and_restores_spendable_funds(self):
        with patch("banking.client.Client.wallet", return_value=self.reply()) as read:
            response = self.post("/api/wallet_balance/")
            again = self.post("/api/wallet_balance/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["available_balance"], "150150.00")
        self.assertTrue(response.json()["transfers_available"])
        self.assertTrue(response.json()["bill_payments_available"])
        self.assertEqual(again.json()["available_balance"], "150150.00")
        read.assert_called_once()

    def test_an_unconfirmed_refresh_does_not_publish_or_spam_the_bank_balance(self):
        with patch("banking.client.Client.wallet", side_effect=ProviderError("network_unavailable")) as read:
            first = self.post("/api/wallet_balance/")
            self.post("/api/wallet_balance/")
        self.assertEqual(first.json()["available_balance"], "0.00")
        read.assert_called_once()

    def test_a_fresh_bank_read_releases_only_a_reconciled_sandbox_opening_review(self):
        self.bank.opening_review_required = True
        self.bank.save(update_fields=["opening_review_required"])
        update_account(self.bank, self.reply())
        self.bank.refresh_from_db()
        self.assertFalse(self.bank.opening_review_required)
        self.assertEqual(self.bank.status, "active")
        self.assertTrue(account_payload(self.user)["spending_available"])

    def test_a_mismatched_balance_or_historical_liability_does_not_release_a_review(self):
        self.bank.opening_review_required = True
        self.bank.save(update_fields=["opening_review_required"])
        update_account(self.bank, self.reply(availableBalance="150149.00"))
        self.assertTrue(self.bank.opening_review_required)
        PreviousBankArchive.objects.create(wallet=self.wallet, balance=50, account_number="old")
        update_account(self.bank, self.reply())
        self.assertTrue(self.bank.opening_review_required)

    @override_settings(NINEPSB={**CONFIG, "SANDBOX_AUTO_VERIFY": True})
    def test_production_opening_reviews_cannot_be_released_by_sandbox_receipts(self):
        self.bank.environment, self.bank.opening_review_required = "production", True
        self.bank.save(update_fields=["environment", "opening_review_required"])
        update_account(self.bank, self.reply())
        self.assertTrue(self.bank.opening_review_required)

    def test_customer_history_keeps_internal_fixture_provenance_out_of_the_label(self):
        response = self.post("/api/user-transaction-history/")
        self.assertEqual(response.json()["all_site_transactions"][0]["service"], "Wallet funding")

    def tier2(self):
        self.bank.tier = 2
        self.bank.save(update_fields=["tier"])
        self.user.nin_hash, self.user.nin_verified = hash_identifier("10987654321"), True
        self.user.save(update_fields=["nin_hash", "nin_verified"])
        record_identity_proof(self.user, "nin", "10987654321", source="9psb_upgrade")
        AccountUpgrade.objects.create(account=self.bank, reference="ZU-TIER2", tier=2,
            bvn_hash=self.user.bvn_hash, nin_hash=self.user.nin_hash, request_digest="tier2", state="approved")
        self.user.recompute_tier()
        self.user.save(update_fields=["tier"])

    def test_tier3_address_and_bank_approval_complete_without_a_physical_provider_call(self):
        self.tier2()
        with patch("banking.address._request") as physical:
            row = start(self.user, ADDRESS)
            self.assertEqual(start(self.user, ADDRESS).pk, row.pk)
        physical.assert_not_called()
        self.assertEqual(unseal(row.encrypted_report)["source"], "sandbox_address_fixture")
        self.assertTrue(verification_status(self.user)["address_verified"])
        body = profile(phoneNumber=PHONE, bvn=None, houseNumber="12")
        with patch("banking.client.Client.wallet", side_effect=[self.reply(2), self.reply(3)]), \
                patch("banking.client.Client.upgrade", return_value=Reply({"status": "SUCCESS"}, 200)) as sent, \
                patch("banking.client.Client.upgrade_status", return_value=Reply({"status": "SUCCESS", "data": {
                    "accountNumber": self.bank.number, "status": "APPROVED"}}, 200)):
            response = self.post("/api/banking/upgrade/", tier=3, profile=body, address_reference=row.reference)
        self.assertTrue(response.json()["success"], response.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 3)
        self.assertTrue(self.user.address_verified)
        self.assertTrue(sent.call_args.args[2]["proofOfAddressVerification"])

    def test_tier3_fixture_does_not_grant_a_tier_before_bank_approval(self):
        self.tier2()
        start(self.user, ADDRESS)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertFalse(self.user.address_verified)

    def test_bank_approval_records_nin_proof_even_when_the_hash_already_existed(self):
        self.user.nin_hash = hash_identifier("10987654321")
        self.user.save(update_fields=["nin_hash"])
        with patch("banking.client.Client.wallet", side_effect=[self.reply(1), self.reply(2)]), \
                patch("banking.client.Client.upgrade", return_value=Reply({"status": "SUCCESS"}, 200)), \
                patch("banking.client.Client.upgrade_status", return_value=Reply({"status": "SUCCESS", "data": {
                    "accountNumber": self.bank.number, "status": "APPROVED"}}, 200)):
            response = self.post("/api/banking/upgrade/", tier=2, profile=profile(phoneNumber=PHONE, bvn=None))
        self.assertTrue(response.json()["success"], response.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tier, 2)
        self.assertTrue(self.user.nin_verified)
        self.assertTrue(self.user.identity_proofs.filter(identity_type="nin", source="9psb_upgrade").exists())

    def test_status_repairs_an_older_approved_upgrade_without_another_submission(self):
        self.tier2()
        self.user.identity_proofs.filter(identity_type="nin").delete()
        self.user.tier, self.user.nin_verified = 1, False
        self.user.save(update_fields=["tier", "nin_verified"])
        with patch("banking.client.Client.wallet", return_value=self.reply(2)), \
                patch("banking.client.Client.upgrade") as sent:
            response = self.post("/api/banking/status/")
        self.assertEqual(response.json()["tier"], 2, response.content)
        self.assertTrue(response.json()["nin_verified"])
        sent.assert_not_called()

    @override_settings(NINEPSB={**CONFIG, "SANDBOX_AUTO_VERIFY": True})
    def test_tier3_fixture_never_applies_in_production(self):
        self.bank.environment = "production"
        self.bank.save(update_fields=["environment"])
        self.tier2()
        with self.assertRaisesRegex(ValueError, "awaiting provider setup"):
            start(self.user, ADDRESS)
        self.assertFalse(AddressVerification.objects.exists())

    def test_deployment_check_has_no_payment_calls_or_private_output(self):
        output = StringIO()
        with patch("banking.management.commands.bank_service_check.sandbox_checks", return_value={"checks": []}), \
                patch("banking.client.Client.wallet", return_value=self.reply()), \
                patch("banking.client.Client.transfer") as transfer, patch("banking.client.Client.topup") as topup:
            call_command("bank_service_check", stdout=output)
        transfer.assert_not_called()
        topup.assert_not_called()
        self.assertIn('"spending_available": true', output.getvalue())
        for private in (PHONE, self.bank.number, self.user.email):
            self.assertNotIn(private, output.getvalue())
