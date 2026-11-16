"""Balances with no Wema account behind them: refused at the source, explained when found.

On 15-16 November 2026 production paged twice a day: "customer balances with no
Wema account behind them" (4 wallets, 2,826.00) and a SHORTFALL of exactly the
same 2,826.00. Every naira a live customer holds must sit in their own Wema
account, so a credit to a wallet without one is a shortfall nothing reconciles.

Pinned here: such a credit is refused on a live deploy, by every path that can
make one (wallet.services.credit and the callers built on it); the settlement
alert names each wallet with where its money came from and the command that
resolves it; and the SHORTFALL line says how much of it those wallets explain.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from wallet.models import Transaction, Wallet
from wallet.services import (UNBACKED_CREDIT_MESSAGE, UnbackedCredit, credit,
                             provision_wema_account, wema_account_reference)
from wallet.tests import make_user
from wallet.unbacked import SIM_CREDIT_PREFIX, unbacked_wallets

NUBAN = "0123456789"


def _live(value=True):
    return mock.patch("utility.wema.wema_live", return_value=value)


@override_settings(VELOCITY_MAX_OUT_10MIN=0)
class UnbackedCreditGuardTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08055550001", "nobank@zitch.app")

    def test_live_credit_to_a_wallet_without_a_wema_account_is_refused(self):
        with _live(), self.assertRaises(UnbackedCredit) as caught:
            credit(self.user, Decimal("500"), "Manual credit — operations")
        self.assertEqual(str(caught.exception), UNBACKED_CREDIT_MESSAGE)
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("0"))
        self.assertFalse(Transaction.objects.filter(user=self.user).exists())

    def test_a_mock_account_number_does_not_count_as_a_bank_account(self):
        Wallet.objects.filter(user=self.user).update(account_number=NUBAN, bank_name="Wema Bank (demo)")
        with _live(), self.assertRaises(UnbackedCredit):
            credit(self.user, Decimal("500"), "Seed")

    def test_live_credit_to_a_wallet_with_its_wema_account_is_allowed(self):
        Wallet.objects.filter(user=self.user).update(
            account_number=NUBAN, account_reference=wema_account_reference(self.user))
        with _live():
            credit(self.user, Decimal("500"), "Seed")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("500"))

    def test_simulation_and_tests_still_credit_freely(self):
        with _live(False):
            credit(self.user, Decimal("500"), "Seed")
        self.assertEqual(Wallet.objects.get(user=self.user).balance, Decimal("500"))

    def test_opening_an_account_with_an_unbacked_balance_tells_the_operator_how_to_settle_it(self):
        Wallet.objects.filter(user=self.user).update(balance=Decimal("700"))
        with mock.patch("utility.alerts.alert") as alert:
            _wallet, outcome = provision_wema_account(self.user, account_number=NUBAN)
        self.assertEqual(outcome, "provisioned")
        alert.assert_called_once()
        self.assertIn(f"prepare_settlement_funding --user-id {self.user.id} --apply",
                      alert.call_args.kwargs["next_step"])
        self.assertEqual(alert.call_args.kwargs["amount"], "700.00")

    def test_opening_an_account_with_no_balance_is_quiet(self):
        with mock.patch("utility.alerts.alert") as alert:
            provision_wema_account(self.user, account_number=NUBAN)
        alert.assert_not_called()


@override_settings(VELOCITY_MAX_OUT_10MIN=0)
class UnbackedOriginTests(TestCase):
    """What each existing unbacked balance is, and what resolves it."""

    def _credit(self, user, amount, service, reference):
        with _live(False):
            credit(user, Decimal(amount), service, reference=reference)

    def test_origins_add_up_to_the_balance_and_name_the_next_step(self):
        sim, _ = make_user("08055550002", "sim@zitch.app")
        self._credit(sim, "1000", "Wallet funding", SIM_CREDIT_PREFIX + "A1")
        real, _ = make_user("08055550003", "real@zitch.app")
        self._credit(real, "1500", "Manual credit — operations", "ZFND-M1")
        self._credit(real, "326", "Transfer from Ada", "ZTRF123-C")
        backed, _ = make_user("08055550004", "backed@zitch.app")
        Wallet.objects.filter(user=backed).update(account_number=NUBAN)
        self._credit(backed, "900", "Seed", "ZFND-B1")

        rows = {r["user_id"]: r for r in unbacked_wallets()}
        self.assertEqual(set(rows), {sim.id, real.id})
        self.assertEqual(rows[sim.id]["origins"], {"simulated": Decimal("1000")})
        self.assertIn("purge_simulation_data", rows[sim.id]["next_step"])
        self.assertEqual(rows[real.id]["origins"],
                         {"manual": Decimal("1500"), "local_transfer": Decimal("326")})
        self.assertIn("prepare_settlement_funding", rows[real.id]["next_step"])

    def test_spent_money_is_the_oldest_money(self):
        user, _ = make_user("08055550005", "spent@zitch.app")
        self._credit(user, "1000", "Wallet funding", SIM_CREDIT_PREFIX + "OLD")
        self._credit(user, "200", "Manual credit — operations", "ZFND-NEW")
        Wallet.objects.filter(user=user).update(balance=Decimal("500"))
        (row,) = unbacked_wallets()
        self.assertEqual(row["origins"], {"manual": Decimal("200"), "simulated": Decimal("300")})

    def test_the_command_lists_each_wallet_and_can_gate(self):
        user, _ = make_user("08055550006", "cmd@zitch.app")
        self._credit(user, "250", "Manual credit — operations", "ZFND-C1")
        out = StringIO()
        with self.assertRaises(SystemExit):
            call_command("unbacked_balances", "--fail-nonzero", stdout=out)
        self.assertIn(f"user {user.id}: 250.00 (manual 250.00)", out.getvalue())
        self.assertNotIn("cmd@zitch.app", out.getvalue())


@override_settings(VELOCITY_MAX_OUT_10MIN=0)
class SettlementReportExplainsTheShortfallTests(TestCase):
    def setUp(self):
        self.backed, _ = make_user("08055550010", "backed2@zitch.app", balance="3737.41")
        Wallet.objects.filter(user=self.backed).update(
            account_number=NUBAN, account_reference=wema_account_reference(self.backed))
        self.orphan, _ = make_user("08055550011", "orphan@zitch.app")
        with _live(False):
            credit(self.orphan, Decimal("2826.00"), "Manual credit — operations", reference="ZFND-O1")

    def _run(self):
        out = StringIO()
        with _live(), mock.patch("utility.wema.get_balance",
                                 return_value={"success": True, "balance_naira": Decimal("3737.41")}), \
                mock.patch("utility.alerts.alert") as alert:
            call_command("settlement_report", stdout=out)
        return out.getvalue(), {c.args[0]: c.kwargs for c in alert.call_args_list}

    def test_the_alerts_name_the_wallet_and_say_the_shortfall_is_fully_explained(self):
        out, alerts = self._run()
        self.assertIn("POSITION -₦2,826.00 (SHORTFALL)", out)
        unbacked = alerts["settlement_report: customer balances with no Wema account behind them"]
        self.assertEqual(unbacked["wallets"], 1)
        self.assertIn(f"user {self.orphan.id}: 2826.00 (manual 2826.00)", unbacked["wallet_detail"])
        self.assertIn("prepare_settlement_funding", unbacked["wallet_detail"])
        shortfall = alerts["settlement_report: SHORTFALL — customer liability exceeds every asset rail combined"]
        self.assertEqual(shortfall["from_wallets_without_wema_account"], "2826.00")
        self.assertEqual(shortfall["unexplained"], "0.00")

    def test_a_gap_beyond_the_unbacked_wallets_is_called_unexplained(self):
        out = StringIO()
        with _live(), mock.patch("utility.wema.get_balance",
                                 return_value={"success": True, "balance_naira": Decimal("3000.00")}), \
                mock.patch("utility.alerts.alert") as alert:
            call_command("settlement_report", stdout=out)
        kwargs = {c.args[0]: c.kwargs for c in alert.call_args_list}[
            "settlement_report: SHORTFALL — customer liability exceeds every asset rail combined"]
        self.assertEqual(kwargs["unexplained"], "737.41")
