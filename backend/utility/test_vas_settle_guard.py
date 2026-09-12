"""A VAS purchase must never be accepted when this deploy could not settle it.

The hazard, end to end. ALAT's airtime/data and bills purchase endpoints may answer
``PROCESSING``. ``settle_or_refund`` correctly HOLDS the money on that — refunding a
maybe-delivered top-up would leak it — and leaves the row for the reconcile cron.
The cron's only tool is ``wema.vas_status``, which answers with a bare integer
``transactionStatus`` that ALAT publishes no legend for; with no
``WEMA_VAS_STATUS_LEGEND`` / ``WEMA_BILLS_STATUS_LEGEND`` configured, ``_parse_vas``
reports ``pending`` for every code it sees, forever. Nothing else can settle the row
either: the bank's own transaction callback routes back through ``vtu_requery``.

So on a legend-less deploy, a PROCESSING purchase is a customer debited with nothing
delivered, nothing refunded, and no job anywhere that can ever clear it. The only
safe answer is to refuse the purchase BEFORE the provider call, where the ordinary
failure path refunds the debit in full — which is what these tests pin.
"""
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from utility import providers as P

# CHANNEL_ID + an airtime subscription key => wema._vas_live("airtime") is True, i.e.
# real calls WILL be made. Note "bills" is wallet-key-covered (wema._WALLET_COVERED),
# so the wallet key alone already makes bill payments live.
_KEYED = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
          "KEYS": {"wallet": "subkey", "airtime": "airkey"},
          "SOURCE_ACCOUNT": "0100000001", "SECURITY_INFO": "sec", "SIMULATION": False}
_AIRTIME_LEGEND = {**_KEYED, "VAS_STATUS_LEGEND": "1=success 2=pending 3=failed"}
_BILLS_LEGEND = {**_KEYED, "BILLS_STATUS_LEGEND": "1=success 2=pending 3=failed"}

_AIRTIME = ("mtn-airtime", {"amount": "500", "phone": "08012345678",
                            "source_account": "0100000001"})


class CanSettleTests(SimpleTestCase):
    @override_settings(WEMA=_KEYED)
    def test_live_without_a_legend_cannot_settle(self):
        ok, why = P.vas_can_settle("airtime")
        self.assertFalse(ok)
        self.assertIn("WEMA_VAS_STATUS_LEGEND", why)

    @override_settings(WEMA=_AIRTIME_LEGEND)
    def test_live_with_a_legend_can_settle(self):
        self.assertEqual(P.vas_can_settle("airtime"), (True, ""))

    @override_settings(WEMA=_KEYED)
    def test_bills_are_gated_on_their_own_legend(self):
        # The two status endpoints answer with DIFFERENT integer enums (1..11 vs 1..9),
        # so an airtime legend cannot decode a bill and must not unblock one.
        with override_settings(WEMA=_AIRTIME_LEGEND):
            self.assertFalse(P.vas_can_settle("bill")[0])
        with override_settings(WEMA=_BILLS_LEGEND):
            self.assertTrue(P.vas_can_settle("bill")[0])

    @override_settings(WEMA=_AIRTIME_LEGEND)
    def test_data_settles_against_the_airtime_legend(self):
        # Data requeries the airtime status endpoint with transactionType=2, so it is
        # the airtime legend that decodes it — not a legend of its own.
        self.assertTrue(P.vas_can_settle("data")[0])

    @override_settings(WEMA={**_KEYED, "CHANNEL_ID": "", "KEYS": {}})
    def test_an_unkeyed_rail_is_not_this_gate_s_business(self):
        # Nothing is stranded: the rail fails closed in production and mocks a
        # synchronous SUCCESS in dev/tests, neither of which needs a requery.
        self.assertTrue(P.vas_can_settle("airtime")[0])

    @override_settings(WEMA={**_AIRTIME_LEGEND, "SIMULATION": True})
    def test_simulation_needs_no_legend(self):
        self.assertTrue(P.vas_can_settle("airtime")[0])


class PurchaseRefusalTests(SimpleTestCase):
    @override_settings(WEMA=_KEYED)
    def test_an_unsettleable_airtime_purchase_never_reaches_the_bank(self):
        with mock.patch("utility.wema.purchase_airtime") as buy, \
             mock.patch("utility.alerts.alert") as alert:
            res = P.vtu_purchase(*_AIRTIME, reference="R1")
        buy.assert_not_called()
        self.assertFalse(res["success"])
        # pending MUST be falsy, or settle_or_refund would hold the debit instead of
        # refunding it — the exact outcome this guard exists to prevent.
        self.assertFalse(res.get("pending"))
        self.assertIn("not been charged", res["message"])
        self.assertIn("WEMA_VAS_STATUS_LEGEND", res["unsettleable"])
        self.assertTrue(alert.called)   # an operator has to learn why sales stopped

    @override_settings(WEMA=_AIRTIME_LEGEND)
    def test_a_settleable_purchase_goes_through_untouched(self):
        with mock.patch("utility.wema.purchase_airtime",
                        return_value={"success": True, "status": "SUCCESS"}) as buy:
            res = P.vtu_purchase(*_AIRTIME, reference="R2")
        buy.assert_called_once()
        self.assertTrue(res["success"])
        self.assertEqual(res["vas_rail"], "wema")

    @override_settings(WEMA=_AIRTIME_LEGEND)
    def test_a_bill_is_refused_when_only_the_airtime_legend_is_set(self):
        route = {"type": "bill", "code": "PKG-1", "amount": "2500"}
        with mock.patch("utility.providers._wema_vas_route", return_value=route), \
             mock.patch("utility.wema.pay_bill") as pay, \
             mock.patch("utility.alerts.alert"):
            res = P.vtu_purchase("ikeja-electric",
                                 {"amount": "2500", "billersCode": "1234567890",
                                  "source_account": "0100000001"}, reference="R3")
        pay.assert_not_called()
        self.assertFalse(res["success"])
        self.assertFalse(res.get("pending"))

    @override_settings(WEMA={**_KEYED, "CHANNEL_ID": "", "KEYS": {}})
    def test_the_dev_mock_path_is_not_broken_by_the_guard(self):
        res = P.vtu_purchase(*_AIRTIME, reference="R4")
        self.assertTrue(res["success"])
        self.assertTrue(res.get("mock"))


@override_settings(VELOCITY_MAX_OUT_10MIN=0, WEMA=_KEYED)
class RefusalRefundsTests(TestCase):
    """The money property: a refused purchase leaves the customer whole."""

    def test_the_debit_is_refunded_not_left_pending(self):
        from wallet.models import Transaction
        from wallet.services import run_provider_purchase
        from wallet.tests import make_user

        user, _ = make_user("08044440009", "settle@zitch.app", balance="5000", tier=3)

        with mock.patch("utility.wema.purchase_airtime") as buy, \
             mock.patch("utility.alerts.alert"):
            status, txn, _res = run_provider_purchase(
                user, Decimal("500"), "Airtime MTN 500", {},
                lambda ref: P.vtu_purchase(
                    "mtn-airtime",
                    {"amount": "500", "phone": "08012345678",
                     "source_account": "0100000001"}, ref),
            )

        buy.assert_not_called()
        self.assertEqual(status, "failed")
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        user.wallet.refresh_from_db()
        self.assertEqual(user.wallet.balance, Decimal("5000"))
        # …and it is NOT left flagged for a reconcile that could never resolve it.
        self.assertFalse((txn.meta or {}).get("reconcile"))


@override_settings(VELOCITY_MAX_OUT_10MIN=0)
class RetiredRailRequeryTests(TestCase):
    """A pending row from the retired rail is not requeried against the partner bank."""

    def _pending(self, vas_rail):
        from wallet.services import debit
        from wallet.tests import make_user

        user, _ = make_user("08044440010", "requery@zitch.app", balance="5000", tier=3)
        meta = {"vas_type": "airtime", "reconcile": True}
        if vas_rail:
            meta["vas_rail"] = vas_rail
        return debit(user, Decimal("300"), "Airtime MTN 300", meta=meta)

    def test_a_retired_rail_row_is_left_pending_and_paged(self):
        txn = self._pending("vtung")
        with mock.patch("utility.wema.vas_status") as status, \
             mock.patch("utility.alerts.alert") as alert:
            res = P.vtu_requery(txn.reference)
        status.assert_not_called()
        self.assertEqual(res["status"], "RETIRED_RAIL")
        # Never auto-refunded: the purchase may well have been delivered pre-cutover.
        self.assertTrue(res["pending"])
        self.assertFalse(res["success"])
        self.assertIn("retired VAS rail", alert.call_args[0][0])

    def test_the_page_is_cooled_so_a_two_minute_cron_does_not_storm(self):
        txn = self._pending("vtung")
        with mock.patch("utility.wema.vas_status"), \
             mock.patch("utility.alerts.alert") as alert:
            for _ in range(5):
                P.vtu_requery(txn.reference)
        self.assertEqual(alert.call_count, 1)

    def test_the_reconcile_cron_cannot_bypass_the_guard(self):
        # The cron is the only AUTOMATED settlement path, and it used to call
        # wema.vas_status directly — so the guard has to sit on the route it takes.
        from django.core.management import call_command

        from datetime import timedelta

        from django.utils import timezone

        from wallet.models import Transaction

        txn = self._pending("vtung")
        # Age it past the requery cutoff, or the sweep would skip it and this test
        # would pass without ever reaching the guard.
        Transaction.objects.filter(pk=txn.pk).update(
            created=timezone.now() - timedelta(minutes=10))

        out = StringIO()
        with mock.patch("utility.wema.vas_status") as status, \
             mock.patch(
                 "utility.management.commands.reconcile_wema.wema_provisioned_wallets",
                 return_value=[]), \
             mock.patch("utility.alerts.alert") as alert:
            call_command("reconcile_wema", "--lookback-days=0", stdout=out)

        self.assertIn("VAS checked 1", out.getvalue())   # the sweep DID pick it up…
        status.assert_not_called()                       # …and still never asked the bank
        self.assertTrue(any("retired VAS rail" in c[0][0] for c in alert.call_args_list))
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.PENDING)

    def test_the_result_does_not_rewrite_the_row_meta_every_pass(self):
        # settle_or_refund persists vas_rail from a pending result; reporting the rail
        # under that key would rewrite meta on every two-minute pass for no gain.
        txn = self._pending("vtung")
        with mock.patch("utility.alerts.alert"):
            res = P.vtu_requery(txn.reference)
        self.assertNotIn("vas_rail", res)
        self.assertEqual(res["retired_rail"], "vtung")

    def test_a_partner_bank_row_is_requeried_normally(self):
        txn = self._pending("wema")
        with mock.patch("utility.wema.vas_status",
                        return_value={"success": True, "pending": False}) as status:
            res = P.vtu_requery(txn.reference)
        status.assert_called_once_with(txn.reference, "airtime")
        self.assertTrue(res["success"])

    def test_a_row_with_no_recorded_rail_is_still_requeried(self):
        # Unchanged behaviour, deliberately: absent metadata is not evidence of the
        # retired rail, and the partner-bank status check is money-safe either way.
        txn = self._pending(None)
        with mock.patch("utility.wema.vas_status",
                        return_value={"success": False, "pending": True}) as status:
            P.vtu_requery(txn.reference)
        status.assert_called_once()
