"""A new bank opening review stays visible without mislabelling old funds."""
from decimal import Decimal
from django.test import TestCase, override_settings
from banking.models import BankAccount, PreviousBankArchive
from banking.test_client import CONFIG
from banking.test_fixtures import make_bank_user
from wallet.models import Transaction
from wallet.services import LimitExceeded, customer_funding_account, debit, wallet_balance_payload


@override_settings(NINEPSB={**CONFIG, "TRANSFER_FEE_TOTAL": "0"})
class BankHistoryReviewVisibilityTests(TestCase):
    def setUp(self):
        self.user, self.token = make_bank_user("08099990216", "history-review@example.test", balance="1000", tier=2)
        self.wallet = self.user.wallet
        BankAccount.objects.filter(wallet=self.wallet).update(opening_review_required=True)

    def test_opening_review_is_visible_without_manufacturing_historical_money(self):
        state = customer_funding_account(self.user)
        self.assertEqual(state["account_setup_state"], "bank_history_review")
        self.assertTrue(state["can_fund"])
        self.assertIn("needs review", state["migration_message"])
        for flag in ("spending_available", "transfers_available", "bill_payments_available"):
            self.assertFalse(state[flag])
        balances = wallet_balance_payload(self.user)
        self.assertEqual(balances["balance"], Decimal("1000"))
        self.assertEqual(balances["available_balance"], Decimal("0"))
        self.assertEqual(balances["restricted_balance"], Decimal("1000"))
        self.assertEqual(balances["historical_balance"], Decimal("0"))
        self.assertFalse(PreviousBankArchive.objects.exists())

    def test_both_api_responses_report_the_same_current_bank_review(self):
        for path in ("/api/wallet/account/", "/api/wallet_balance/"):
            with self.subTest(path=path):
                response = self.client.post(path, {"access_token": self.token}, content_type="application/json")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["account_setup_state"], "bank_history_review")
                self.assertFalse(response.json()["transfers_available"])

    def test_review_refuses_new_debits_without_hiding_existing_receipts(self):
        for service in ("Transfer to Ada", "Airtime — MTN"):
            with self.subTest(service=service), self.assertRaises(LimitExceeded):
                debit(self.user, "1", service)
        self.assertFalse(Transaction.objects.filter(direction=Transaction.OUT).exists())
        self.assertEqual(Transaction.objects.filter(direction=Transaction.IN).count(), 1)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("1000"))

    def test_certified_current_account_keeps_old_money_visible_but_unspendable(self):
        PreviousBankArchive.objects.create(wallet=self.wallet, balance="900", account_number="old")
        BankAccount.objects.filter(wallet=self.wallet).update(opening_review_required=False)
        self.assertEqual(customer_funding_account(self.user)["account_setup_state"], "ready")
        balances = wallet_balance_payload(self.user)
        self.assertEqual(balances["balance"], Decimal("1000"))
        self.assertEqual(balances["available_balance"], Decimal("100"))
        self.assertEqual(balances["historical_balance"], Decimal("900"))

    def test_missing_current_account_requests_bank_setup(self):
        from wallet.tests import make_user
        self.user, _ = make_user("08099990217", "no-bank@example.test")
        self.assertEqual(customer_funding_account(self.user)["account_setup_state"], "setup_required")
        self.assertFalse(customer_funding_account(self.user)["has_account"])

    def test_old_simulation_flag_cannot_remove_a_current_bank_review(self):
        with override_settings(WEMA={"SIMULATION": True}):
            self.assertEqual(customer_funding_account(self.user)["account_setup_state"], "bank_history_review")
            self.assertEqual(wallet_balance_payload(self.user)["available_balance"], Decimal("0"))
