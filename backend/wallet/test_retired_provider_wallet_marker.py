"""A retained 9PSB wallet NUBAN cannot be advertised after archive cleanup."""
from unittest.mock import patch

from django.test import TestCase

from wallet.models import Wallet
from wallet.partnership import legacy_funding_blockers, partnership_allowed
from wallet.services import apply_wema_credit, customer_funding_account, wema_provisioned_wallets
from wallet.tests import make_user


class RetiredProviderWalletMarkerTests(TestCase):
    def setUp(self):
        self.user, _ = make_user(
            "08011116543", "orphaned-wallet-bank@example.test", balance="100")
        self.wallet = Wallet.objects.get(user=self.user)

    def test_retired_bank_label_blocks_funding_without_old_bank_table(self):
        for label in ("9PSB", "9 Payment Service Bank", "9-Payment Service Bank", "Wema Bank VAS"):
            with self.subTest(label=label):
                Wallet.objects.filter(pk=self.wallet.pk).update(
                    account_number="9012345678", bank_name=label)
                # A provider archive can be deleted or temporarily unavailable;
                # the wallet's old bank label must independently hold the NUBAN.
                with patch("wallet.partnership.previous_provider_cutover_blockers",
                           return_value=[]) as older:
                    self.assertIn("retired_provider_wallet_review",
                                  legacy_funding_blockers(self.user))
                    self.assertFalse(partnership_allowed(self.user))
                    funding = customer_funding_account(self.user)
                self.assertEqual(funding["account_number"], "")
                self.assertFalse(funding["can_fund"])
                self.assertEqual(funding["account_setup_state"], "legacy_funding_review")
                older.assert_not_called()

    def test_old_reference_blocks_relabelled_account_on_both_funding_paths(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0451234567", bank_name="Wema Bank", account_reference="9PSB-1")
        self.wallet.refresh_from_db()
        self.assertFalse(partnership_allowed(self.user))
        self.assertFalse(wema_provisioned_wallets().filter(pk=self.wallet.pk).exists())
        self.assertIsNone(apply_wema_credit(self.wallet, {
            "referenceId": "retired-ref", "amount": "20", "creditType": "Credit", "status": "Successful",
        }))
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, 100)

    def test_wema_bank_label_is_not_incorrectly_marked_as_retired(self):
        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0123456789", bank_name="Wema Bank")
        with patch("wallet.partnership.previous_provider_cutover_blockers",
                   return_value=[]):
            self.assertEqual(legacy_funding_blockers(self.user), [])

    def test_inventory_separates_primary_mappings_from_retired_evidence(self):
        from utility.management.commands.partnership_diagnostics import account_inventory

        Wallet.objects.filter(pk=self.wallet.pk).update(
            account_number="0451234567", bank_name="Wema Bank", account_reference="9PSB-1")
        inventory = account_inventory("0451234567")
        self.assertEqual(inventory["wallets_with_primary_numbers"], 1)
        self.assertEqual(inventory["wallets_with_retired_markers"], 1)
        self.assertEqual(inventory["wallets_with_secondary_account_data"], 0)
