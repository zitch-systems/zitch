"""Tests for settlement_report — the whole-float position across every asset rail.

Since VAS moved to the partner bank there are exactly two pots: the customer NUBANs
and the pool account. The invariants pinned here are (a) the pool counts toward what
we hold even though no per-wallet check ever reads it, (b) a rail we could not read
makes the position advisory rather than inventing a shortfall out of an outage, and
(c) VAS spend now debits the buyer's OWN NUBAN, so it drops what we hold and what we
owe together and leaves the position untouched — which is precisely why the old
third rail, an externally-held VAS float with a standing sweep obligation, is gone.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from wallet.models import Transaction, Wallet
from wallet.services import debit, wema_account_reference
from wallet.tests import make_user

POOL = "9999999999"
NUBAN = "0123456789"


def _balances(mapping):
    """A get_balance stub keyed by account number; anything unmapped is unreadable."""
    def _get(account_number):
        if account_number in mapping:
            return {"success": True, "balance_naira": Decimal(mapping[account_number])}
        return {"success": False, "message": "unreachable"}
    return _get


@override_settings(VELOCITY_MAX_OUT_10MIN=0)
class SettlementReportTests(TestCase):
    def setUp(self):
        self.user, _ = make_user("08044440001", "sr@zitch.app", balance="5000", tier=3)
        Wallet.objects.filter(user=self.user).update(
            account_reference=wema_account_reference(self.user), account_number=NUBAN)

    def _run(self, *args, bank=None, live=True):
        out, err = StringIO(), StringIO()
        code = 0
        with mock.patch("utility.wema.wema_live", return_value=live), \
             mock.patch("utility.wema.get_balance",
                        side_effect=_balances(bank if bank is not None else {})), \
             mock.patch("utility.alerts.alert") as alert_mock:
            try:
                call_command("settlement_report", *args, stdout=out, stderr=err)
            except SystemExit as exc:
                code = exc.code
        return out.getvalue(), alert_mock, code

    # --- the position ------------------------------------------------------

    def test_balanced_position_does_not_page(self):
        # Owed 5,000; the NUBAN holds exactly 5,000.
        out, alert_mock, code = self._run(bank={NUBAN: "5000"})
        self.assertIn("ledger liability ₦5,000.00", out)
        self.assertIn("POSITION +₦0.00 (surplus)", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_shortfall_pages(self):
        # The solvency signal: we owe 5,000 and hold 4,000 across every rail.
        out, alert_mock, code = self._run("--fail-on-breach", bank={NUBAN: "4000"})
        self.assertIn("POSITION -₦1,000.00 (SHORTFALL)", out)
        self.assertEqual(code, 1)
        message = alert_mock.call_args[0][0]
        self.assertIn("SHORTFALL", message)
        self.assertEqual(alert_mock.call_args[1]["level"], "error")

    def test_vas_spend_leaves_the_position_flat_with_no_third_rail(self):
        # The reason the externally-held VAS float is gone. Partner-bank VAS debits the
        # buyer's own NUBAN, so a 1,200 airtime buy drops the ledger liability to 3,800
        # AND the NUBAN to 3,800: held and owed fall together and the position does not
        # move. On the retired rail the NUBAN would still have held 5,000 against 3,800
        # owed, and the missing 1,200 sat in a pot at another company that this report
        # had to read — and page about when it could not.
        debit(self.user, Decimal("1200"), "Airtime MTN 1200")
        out, alert_mock, code = self._run(bank={NUBAN: "3800"})
        self.assertIn("ledger liability ₦3,800.00", out)
        self.assertIn("POSITION +₦0.00 (surplus)", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_pool_account_counts_toward_what_we_hold(self):
        # Pool-sourced payouts are paid from WEMA_SOURCE_ACCOUNT, which belongs to no
        # user — so no per-wallet check ever looks at it.
        from django.conf import settings as dj_settings
        with mock.patch.dict(dj_settings.WEMA, {"SOURCE_ACCOUNT": POOL}):
            out, alert_mock, code = self._run(bank={NUBAN: "1000", POOL: "4000"})
        self.assertIn("pool        ₦4,000.00", out)
        self.assertIn("POSITION +₦0.00 (surplus)", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_surplus_pages_only_above_the_configured_ceiling(self):
        out, alert_mock, code = self._run(bank={NUBAN: "9000"})
        self.assertIn("POSITION +₦4,000.00 (surplus)", out)
        alert_mock.assert_not_called()   # no ceiling set -> surplus never pages
        self.assertEqual(code, 0)

        out, alert_mock, code = self._run("--max-surplus=1000", "--fail-on-breach",
                                         bank={NUBAN: "9000"})
        self.assertEqual(code, 1)
        self.assertIn("surplus above the configured ceiling", alert_mock.call_args[0][0])
        self.assertEqual(alert_mock.call_args[1]["level"], "warning")

    def test_shortfall_within_tolerance_does_not_page(self):
        out, alert_mock, code = self._run("--max-shortfall=100", "--fail-on-breach",
                                          bank={NUBAN: "4950"})
        self.assertIn("SHORTFALL", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    # --- an unreadable rail makes the position advisory, not authoritative --

    def test_unreadable_pool_rail_is_flagged_not_silently_zeroed(self):
        # The pool is configured but its balance would not read. Counting an unreadable
        # rail as 0 would invent a shortfall out of an outage and page for solvency.
        from django.conf import settings as dj_settings
        with mock.patch.dict(dj_settings.WEMA, {"SOURCE_ACCOUNT": POOL}):
            out, alert_mock, code = self._run("--fail-on-breach", bank={NUBAN: "5000"})
        self.assertIn("pool        UNREADABLE", out)
        self.assertEqual(code, 1)
        levels = [c[1].get("level") for c in alert_mock.call_args_list]
        messages = [c[0][0] for c in alert_mock.call_args_list]
        self.assertIn("warning", levels)
        self.assertTrue(any("unreadable rail" in m for m in messages))
        # and it must NOT have paged a shortfall off the back of the missing rail
        self.assertFalse(any("SHORTFALL" in m for m in messages))

    def test_unreachable_nuban_is_flagged(self):
        out, alert_mock, code = self._run(bank={})  # NUBAN itself unreadable
        self.assertIn("1 unreachable", out)
        self.assertTrue(any("unreadable rail" in c[0][0] for c in alert_mock.call_args_list))

    # --- rail attribution -------------------------------------------------

    def test_vas_spend_is_reported_separately_from_a_bank_payout(self):
        # Both leave the same bank now, but they are still split out: VAS out is the
        # line that explains a day's outflow, and a VAS total climbing while the
        # position does not move is the signature of debits that never reached a biller.
        debit(self.user, Decimal("1200"), "Airtime MTN 1200")
        out, _alert, _code = self._run(bank={NUBAN: "3800"})
        self.assertIn("VAS out ₦1,200.00", out)
        self.assertIn("bank out ₦0.00", out)
        self.assertNotIn("sweep", out)   # no externally-held float to sweep to

    def test_bank_payout_is_a_bank_outflow(self):
        debit(self.user, Decimal("800"), "Transfer to ADEYEMI",
              meta={"bank": "058", "account": "0011223344"})
        out, _alert, _code = self._run(bank={NUBAN: "4200"})
        self.assertIn("bank out ₦800.00", out)
        self.assertIn("VAS out ₦0.00", out)

    def test_internal_transfer_leaves_no_bank(self):
        # No meta.bank -> not a payout; "Transfer to" -> internal. It moves liability
        # between two of our users and nets to zero at every asset rail.
        debit(self.user, Decimal("700"), "Transfer to Chidi")
        out, _alert, _code = self._run(bank={NUBAN: "5000"})
        self.assertIn("internal ₦700.00", out)
        self.assertIn("bank out ₦0.00", out)
        self.assertIn("VAS out ₦0.00", out)

    # --- mock mode --------------------------------------------------------

    def test_no_position_when_not_live(self):
        # Mock get_balance returns 0.00 for everyone, so a position here would report
        # the entire liability as a shortfall.
        out, alert_mock, code = self._run(live=False)
        self.assertIn("no position is computed", out)
        self.assertIn("ledger liability ₦5,000.00", out)  # the real half still prints
        self.assertNotIn("POSITION", out)
        alert_mock.assert_not_called()
        self.assertEqual(code, 0)

    def test_writes_an_audit_row(self):
        self._run(bank={NUBAN: "5000"})
        from whatsapp.models import AuditLog
        row = AuditLog.objects.filter(action="recon.settlement_report").first()
        self.assertIsNotNone(row)
        self.assertEqual(row.after["position"], "0.00")

    def test_failed_debits_do_not_count_as_owed_or_spent(self):
        txn = debit(self.user, Decimal("500"), "Airtime MTN 500")
        txn.transaction_status = Transaction.FAILED
        txn.save(update_fields=["transaction_status"])
        out, _alert, _code = self._run(bank={NUBAN: "5000"})
        self.assertIn("ledger liability ₦5,000.00", out)
        self.assertIn("VAS out ₦0.00", out)
