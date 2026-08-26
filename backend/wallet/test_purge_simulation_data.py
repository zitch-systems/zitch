"""`manage.py purge_simulation_data` — the go-live cleanup for WEMA_SIMULATION.

The cases that matter are the ones where a wrong answer costs money or history:
a fabricated credit must not survive into a live deploy, and a wallet that has
spent real money must not be silently rewritten to make the books balance.
"""
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from wallet.models import Transaction, Wallet

User = get_user_model()


def _run(*args):
    out, err = StringIO(), StringIO()
    call_command("purge_simulation_data", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def _user(phone, **kw):
    return User.objects.create(username=phone, phone=phone, **kw)


def _sim_credit(user, amount="5000.00", ref="WEMA-CR-SIM-ABC123"):
    return Transaction.objects.create(
        user=user, service="funding", amount=Decimal(amount), direction=Transaction.IN,
        transaction_status=Transaction.SUCCESS, reference=ref)


def _real_debit(user, amount="1000.00", ref="VTU-REAL-1"):
    return Transaction.objects.create(
        user=user, service="airtime", amount=Decimal(amount), direction=Transaction.OUT,
        transaction_status=Transaction.SUCCESS, reference=ref)


class PurgeSimulationDataTests(TestCase):
    def test_reports_nothing_on_a_clean_database(self):
        self.assertIn("No simulation data found", _run())

    def test_dry_run_changes_nothing(self):
        user = _user("+2348010000001", tier=3, bvn_verified=True)
        wallet = Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                                       account_number="0111111111",
                                       bank_name="Wema Bank (demo)")
        _sim_credit(user)

        out = _run()

        self.assertIn("DRY RUN", out)
        self.assertFalse(Transaction.objects.filter(
            reference__startswith="SIMREV-").exists())
        wallet.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("5000.00"))
        self.assertEqual(wallet.account_number, "0111111111")
        self.assertEqual(user.tier, 3)
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-SIM-ABC123").exists())

    def test_confirm_removes_the_fabricated_money_and_the_mock_nuban(self):
        user = _user("+2348010000002", tier=3, bvn_verified=True, face_verified=True,
                     bvn_last4="1234")
        wallet = Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                                       account_number="0122222222",
                                       account_name="TEST USER",
                                       bank_name="Wema Bank (demo)",
                                       account_reference="ref-2")
        _sim_credit(user)

        _run("--confirm")

        wallet.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(wallet.account_number, "")
        self.assertEqual(wallet.bank_name, "")
        self.assertEqual(wallet.account_reference, "")
        # Reversed, never deleted: the Postgres trigger from migration 0017 raises on
        # any ledger DELETE, so the original row MUST survive and be offset instead.
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-SIM-ABC123").exists())
        reversal = Transaction.objects.get(reference="SIMREV-WEMA-CR-SIM-ABC123")
        self.assertEqual(reversal.direction, Transaction.OUT)
        self.assertEqual(reversal.amount, Decimal("5000.00"))
        self.assertEqual(reversal.transaction_status, Transaction.SUCCESS)
        # The simulated identity is withdrawn, so no tier limit rides on it.
        self.assertEqual(user.tier, 0)
        self.assertFalse(user.bvn_verified)
        self.assertFalse(user.face_verified)
        self.assertEqual(user.bvn_last4, "")
        self.assertFalse(user.is_active)

    def test_keep_active_leaves_the_account_usable(self):
        user = _user("+2348010000003", tier=3)
        Wallet.objects.create(user=user, balance=Decimal("100.00"),
                              account_number="0133333333", bank_name="Wema Bank (demo)")

        _run("--confirm", "--keep-active")

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(user.tier, 0)

    def test_a_wallet_with_real_ledger_rows_is_never_touched(self):
        """The case that would otherwise drive the balance negative.

        Fake naira spent on a live rail leaves a real OUT row. Deleting the credit
        that funded it would recompute a negative balance — which the DB check
        constraint refuses anyway — so this must be reported, not rewritten.
        """
        user = _user("+2348010000004", tier=3, bvn_verified=True)
        wallet = Wallet.objects.create(user=user, balance=Decimal("4000.00"),
                                       account_number="0144444444",
                                       bank_name="Wema Bank (demo)")
        _sim_credit(user)
        _real_debit(user)

        out = _run("--confirm")

        self.assertIn("NEEDS A HUMAN", out)
        wallet.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("4000.00"))
        self.assertEqual(wallet.account_number, "0144444444")
        self.assertEqual(user.tier, 3)
        self.assertTrue(Transaction.objects.filter(reference="WEMA-CR-SIM-ABC123").exists())

    def test_a_real_wallet_with_no_markers_is_invisible_to_the_command(self):
        user = _user("+2348010000005", tier=3)
        Wallet.objects.create(user=user, balance=Decimal("9000.00"),
                              account_number="0155555555", bank_name="Wema Bank")
        _real_debit(user, ref="VTU-REAL-2")

        self.assertIn("No simulation data found", _run("--confirm"))

    def test_a_demo_nuban_with_no_simulated_credits_is_still_purged(self):
        """simulate-kyc without simulate-deposit: no fake money, but a trapped wallet.

        provision refuses to REPLACE an existing NUBAN, so leaving the mock one on
        the wallet means it can never be issued a real account number.
        """
        user = _user("+2348010000006", tier=3)
        wallet = Wallet.objects.create(user=user, balance=Decimal("0.00"),
                                       account_number="0166666666",
                                       bank_name="Wema Bank (demo)")

        _run("--confirm")

        wallet.refresh_from_db()
        self.assertEqual(wallet.account_number, "")

    def test_a_real_ledger_is_never_rewritten_by_the_recompute(self):
        """The guard must fire BEFORE the balance recompute, not after."""
        user = _user("+2348010000007")
        wallet = Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                                       bank_name="Wema Bank (demo)",
                                       account_number="0177777777")
        _sim_credit(user)
        Transaction.objects.create(
            user=user, service="funding", amount=Decimal("3000.00"),
            direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
            reference="WEMA-CR-REAL-1")
        Transaction.objects.create(
            user=user, service="transfer", amount=Decimal("500.00"),
            direction=Transaction.OUT, transaction_status=Transaction.PENDING,
            reference="PAYOUT-REAL-1")

        out = _run("--confirm")

        self.assertIn("NEEDS A HUMAN", out)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("5000.00"))

    def test_fail_nonzero_gates_go_live(self):
        user = _user("+2348010000008")
        Wallet.objects.create(user=user, balance=Decimal("0.00"),
                              account_number="0188888888", bank_name="Wema Bank (demo)")

        with self.assertRaises(SystemExit):
            _run("--fail-nonzero")

        # Once purged there is nothing left to gate on.
        _run("--confirm")
        self.assertIn("No simulation data found", _run("--fail-nonzero"))

    def test_running_twice_does_not_reverse_the_same_credit_again(self):
        """A second reversal would drive the balance negative on a cleaned account."""
        user = _user("+2348010000009")
        wallet = Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                                       account_number="0199999999",
                                       bank_name="Wema Bank (demo)")
        _sim_credit(user)

        _run("--confirm")
        out = _run("--confirm")

        self.assertIn("No simulation data found", out)
        self.assertEqual(
            Transaction.objects.filter(reference__startswith="SIMREV-").count(), 1)
        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, Decimal("0.00"))

    def test_the_reversal_is_not_mistaken_for_real_activity(self):
        """The command's own footprint must not reclassify the account as needing review."""
        user = _user("+2348010000010")
        Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                              account_number="0200000000", bank_name="Wema Bank (demo)")
        _sim_credit(user)

        _run("--confirm")
        out = _run()

        self.assertNotIn("NEEDS A HUMAN", out)

    def test_balance_after_purge_matches_the_ledger(self):
        """integrity_check must stay green immediately after a purge."""
        from wallet.services import wallet_expected_balance

        user = _user("+2348010000011")
        wallet = Wallet.objects.create(user=user, balance=Decimal("5000.00"),
                                       account_number="0211111111",
                                       bank_name="Wema Bank (demo)")
        _sim_credit(user)

        _run("--confirm")

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, wallet_expected_balance(user.id))
