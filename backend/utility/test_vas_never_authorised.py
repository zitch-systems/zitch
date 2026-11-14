"""A purchase the bank never asked to authorise goes back to the customer.

What happened, on 14 November 2026. A customer bought 70 naira of airtime on
WhatsApp while the bank's gateway was answering every call with something that
was not JSON. An unreadable answer does not say whether the bank took the
instruction, so the debit was held, correctly. Then nothing could end the hold:

* Single Account purchases are settled only by the bank's callbacks.
* The bank sends no callback for a request it never processed.
* The operator's requery asked the same question and got the same "awaiting
  callback", so a person could not return the money either.

The bank debits a purchase only after asking this deploy to authorise it, and
that callback refuses any row older than AUTH_MAX_AGE. A purchase past that age
that was never authorised was therefore not made and cannot be made later.
"""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from utility import providers as P
from utility import wema
from wallet.models import BankHistoryCheckpoint, Transaction
from wallet.services import run_provider_purchase, settle_or_refund
from whatsapp.ops import record_webhook

BANK_IP = "135.236.18.76"
LIVE = {"BASE_URL": "https://apiplayground.alat.ng", "CHANNEL_ID": "chan-1",
        "KEYS": {"wallet": "subkey", "airtime": "airkey"},
        "SOURCE_ACCOUNT": "0100000001", "SECURITY_INFO": "sec", "SIMULATION": False,
        "CALLBACK_TOKEN": "test-token", "CALLBACK_ENFORCE_IPS": True,
        "CALLBACK_IPS": [BANK_IP], "AUTH_REQUIRE_SECURITY_INFO": True, "AUTH_MAX_AGE": 900,
        "VAS_STATUS_LEGEND": ("200=success 400=failed "
                              "401=unauthorized_authentication_failed_or_invalid_api")}
UNANSWERED = {"success": False, "pending": True, "message": "Bank gateway unreachable"}


@override_settings(VELOCITY_MAX_OUT_10MIN=0, WEMA=LIVE)
class NeverAuthorisedPurchaseTests(TestCase):
    def setUp(self):
        from wallet.tests import make_user

        self.user, _ = make_user("08044440070", "airtime@zitch.app", balance="5000", tier=3)
        self.user.wallet.account_number = "0155500070"
        self.user.wallet.save(update_fields=["account_number"])
        BankHistoryCheckpoint.objects.create(
            wallet=self.user.wallet, account_number="0155500070", opening_review_required=False)
        alerts = mock.patch("utility.alerts.alert")
        self.alert = alerts.start()
        self.addCleanup(alerts.stop)

    def held_purchase(self, *, minutes_ago=30):
        """An airtime purchase the gateway gave no readable answer to."""
        with mock.patch("utility.wema.purchase_airtime", return_value=dict(UNANSWERED)):
            status, txn, _result = run_provider_purchase(
                self.user, Decimal("70"), "Airtime MTN 70", {},
                lambda ref: P.vtu_purchase(
                    "mtn-airtime", {"amount": "70", "phone": "08166938327",
                                    "source_account": "0155500070"}, ref))
        self.assertEqual(status, "pending")
        Transaction.objects.filter(pk=txn.pk).update(
            created=timezone.now() - timedelta(minutes=minutes_ago))
        txn.refresh_from_db()
        return txn

    def balance(self):
        self.user.wallet.refresh_from_db()
        return self.user.wallet.balance

    def test_the_debit_is_held_while_the_bank_could_still_ask(self):
        for minutes in (1, 14, 19):  # 19: past the 15-minute window, inside the grace
            with self.subTest(minutes=minutes):
                txn = self.held_purchase(minutes_ago=minutes)
                result = P.vtu_requery(txn.reference)
                self.assertEqual(result["status"], "AWAITING_BANK_CALLBACK")
                self.assertEqual(settle_or_refund(txn, result), "pending")
        self.assertEqual(self.balance(), Decimal("5000") - 3 * Decimal("70"))

    def test_a_purchase_the_bank_never_asked_about_is_returned(self):
        txn = self.held_purchase()
        self.assertEqual(self.balance(), Decimal("4930"))

        with self.assertLogs("zitch", level="WARNING") as logs:
            result = P.vtu_requery(txn.reference)
        self.assertEqual((result["success"], result["pending"]), (False, False))
        self.assertEqual(result["status"], "NEVER_AUTHORISED")
        self.assertIn(f"wema_vas_never_authorised ref={txn.reference}", "\n".join(logs.output))
        self.assertTrue(any("vas_purchase_never_authorised" in str(call.args[0])
                            for call in self.alert.call_args_list))

        self.assertEqual(settle_or_refund(txn, result), "failed")
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(txn.meta["failure_status"], "NEVER_AUTHORISED")
        self.assertEqual(self.balance(), Decimal("5000"))

    def test_the_bank_cannot_be_authorised_for_it_before_or_after_the_return(self):
        """The fact the release rests on, and the one that keeps it true afterwards."""
        from wallet.wema_callbacks import _authorize_payout

        txn = self.held_purchase()
        security_info = wema.security_info_for_reference(txn.reference)
        self.assertEqual(_authorize_payout(txn.reference, security_info, BANK_IP),
                         (False, "stale"))
        settle_or_refund(txn, P.vtu_requery(txn.reference))
        self.assertEqual(_authorize_payout(txn.reference, security_info, BANK_IP),
                         (False, f"state_{Transaction.FAILED}"))
        self.assertEqual(self.balance(), Decimal("5000"))

    def test_a_purchase_the_bank_was_authorised_for_is_left_to_the_bank(self):
        from wallet.wema_callbacks import _authorize_payout

        txn = self.held_purchase(minutes_ago=1)
        self.assertEqual(_authorize_payout(
            txn.reference, wema.security_info_for_reference(txn.reference), BANK_IP),
            (True, "authorized"))
        Transaction.objects.filter(pk=txn.pk).update(created=timezone.now() - timedelta(hours=3))
        self.assertEqual(P.vtu_requery(txn.reference)["status"], "AWAITING_BANK_CALLBACK")

    def test_a_purchase_the_bank_has_called_back_about_is_left_to_the_bank(self):
        stamped = self.held_purchase()
        stamped.meta = {**stamped.meta, "wema_callback": {"status": "Pending"}}
        stamped.save(update_fields=["meta"])
        self.assertEqual(P.vtu_requery(stamped.reference)["status"], "AWAITING_BANK_CALLBACK")

        for source, action in (("wema.auth", "denied:security_info_mismatch"),
                               ("wema.txn", "requery_cooled")):
            with self.subTest(source=source):
                txn = self.held_purchase()
                record_webhook(source, verified=True, http_status=200, remote_ip=BANK_IP,
                               reference=txn.reference, action=action, payload={})
                self.assertEqual(P.vtu_requery(txn.reference)["status"],
                                 "AWAITING_BANK_CALLBACK")

    def test_another_purchases_callbacks_do_not_hold_this_one(self):
        txn = self.held_purchase()
        record_webhook("wema.auth", verified=True, http_status=200, remote_ip=BANK_IP,
                       reference="ZTCH-SOMEONE-ELSE", action="authorized:authorized", payload={})
        self.assertEqual(P.vtu_requery(txn.reference)["status"], "NEVER_AUTHORISED")

    def test_the_banks_own_callback_never_triggers_the_return(self):
        """It is recorded only after it is handled, so nothing above has seen it yet."""
        txn = self.held_purchase()
        result = P.vtu_requery(txn.reference, bank_callback=True)
        self.assertEqual(result["status"], "AWAITING_BANK_CALLBACK")

    def test_nothing_is_returned_unless_callbacks_are_authenticated_in_full(self):
        txn = self.held_purchase()
        for missing in ("CALLBACK_TOKEN", "CALLBACK_ENFORCE_IPS", "CALLBACK_IPS",
                        "AUTH_REQUIRE_SECURITY_INFO", "SECURITY_INFO"):
            with self.subTest(missing=missing), override_settings(WEMA={**LIVE, missing: ""}):
                self.assertEqual(P.vtu_requery(txn.reference)["status"],
                                 "AWAITING_BANK_CALLBACK")

    def test_a_requery_without_the_rows_age_returns_nothing(self):
        txn = self.held_purchase()
        result = P.vas_requery(txn.reference, txn.meta, service=txn.service)
        self.assertEqual(result["status"], "AWAITING_BANK_CALLBACK")

    @override_settings(PAYMENT_PROVIDER="wema")
    @mock.patch("utility.management.commands.reconcile_wema.wema_provisioned_wallets",
                return_value=[])
    @mock.patch("wallet.alerts.retry_pending_whatsapp_alerts")
    def test_the_reconcile_job_returns_it(self, _alerts, _wallets):
        txn = self.held_purchase()
        out = StringIO()
        try:
            call_command("reconcile_wema", account_recovery_limit=0, stdout=out)
        except SystemExit:
            pass  # other phases report on a bank this test does not stand up
        txn.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(self.balance(), Decimal("5000"))
        self.assertIn("refunded 1", out.getvalue())
