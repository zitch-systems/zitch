"""A VAS purchase the gateway REFUSED must refund, not hang PENDING forever.

The production failure this closes: ALAT answered every airtime call with HTTP 4xx
and "You've not been profiled to use this service" — the tenant was never entitled
for the Airtime product. That response carries no ``result.status`` string, so
``_parse_vas`` fell into its "no status ⇒ pending" money-safe default and the debit
was left PENDING. Six rows sat there, requeried every ten minutes, never settling and
never refunding, with the customer debited the whole time and the closing screen
saying "processing — we'll confirm shortly".

"Pending" is the right answer for an AMBIGUOUS response. A refusal is not ambiguous:
the gateway understood the request and declined to execute it, which is exactly the
reasoning already written above ``_raise_if_ambiguous`` for why 400/401/403/404/422
are not retried. Nothing was delivered, so the money must go back.
"""
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings

from utility import wema

WEMA_LIVE = {
    "BASE_URL": "https://gw.example",
    "CHANNEL_ID": "chan",
    "KEYS": {"wallet": "wkey", "airtime": "akey", "bills": "bkey"},
    "SOURCE_ACCOUNT": "0100000001",
    "VAS_STATUS_LEGEND": "1=success 2=pending 3=failed",
    "BILLS_STATUS_LEGEND": "1=success 2=pending 3=failed",
}

#: The exact envelope production is getting back: an error message, no status string.
NOT_PROFILED = {"hasError": True, "message": "You've not been profiled to use this service"}


def _response(status_code, payload):
    resp = mock.Mock()
    resp.status_code = status_code
    resp.json.return_value = payload
    return resp


class RefusedPurchaseRefundsTests(SimpleTestCase):
    """The purchase path: any 4xx refusal means nothing was executed."""

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_refused_airtime_purchase_is_a_definitive_failure(self):
        with mock.patch.object(wema, "_post", return_value=_response(403, NOT_PROFILED)):
            res = wema.purchase_airtime(55, "ZTCH-REF-1", "07066737466", "MTN",
                                        source_account="0100000001")
        # Not pending: pending is what stranded the money.
        self.assertFalse(res["pending"], "a refused purchase must not be left pending")
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "REFUSED_403")

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_refused_data_purchase_is_a_definitive_failure(self):
        with mock.patch.object(wema, "_post", return_value=_response(401, NOT_PROFILED)):
            res = wema.purchase_data(500, "ZTCH-REF-2", "07066737466", "MTN", "pkg-1",
                                     source_account="0100000001")
        self.assertFalse(res["pending"])
        self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_every_refusal_code_refunds_on_the_purchase_path(self):
        """400/401/403/404/422 — the set _raise_if_ambiguous deliberately does not
        retry, because the gateway understood the request and refused it."""
        for code in (400, 401, 403, 404, 422):
            with self.subTest(http_status=code):
                with mock.patch.object(wema, "_post",
                                       return_value=_response(code, NOT_PROFILED)):
                    res = wema.purchase_airtime(55, f"ZTCH-{code}", "07066737466", "MTN",
                                                source_account="0100000001")
                self.assertFalse(res["pending"])
                self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_200_with_no_status_is_still_pending(self):
        """The money-safe default must survive: an ambiguous 200 is NOT a refusal,
        and refunding it could double-spend a purchase that was actually delivered."""
        with mock.patch.object(wema, "_post",
                               return_value=_response(200, {"hasError": False})):
            res = wema.purchase_airtime(55, "ZTCH-REF-3", "07066737466", "MTN",
                                        source_account="0100000001")
        self.assertTrue(res["pending"], "an ambiguous response must stay pending")
        self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_successful_purchase_is_unaffected(self):
        with mock.patch.object(wema, "_post", return_value=_response(
                200, {"hasError": False, "result": {"status": "SUCCESS"}})):
            res = wema.purchase_airtime(55, "ZTCH-REF-4", "07066737466", "MTN",
                                        source_account="0100000001")
        self.assertTrue(res["success"])
        self.assertFalse(res["pending"])


class RefusedRequeryTests(SimpleTestCase):
    """The requery path refuses more narrowly: the refusal is of the QUERY, and only
    a refusal of the whole product also proves the purchase could not have run."""

    @override_settings(WEMA=WEMA_LIVE)
    def test_an_unentitled_product_settles_the_stuck_row_as_failed(self):
        """This is what clears the six stuck production rows: we hold no entitlement
        for the product, so the purchase the row is asking about never ran either."""
        for code in (401, 403):
            with self.subTest(http_status=code):
                with mock.patch.object(wema, "_post",
                                       return_value=_response(code, NOT_PROFILED)):
                    res = wema.vas_status("ZTCH12083E287CEE", "airtime")
                self.assertFalse(res["pending"], "an un-entitled product cannot stay pending")
                self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_refused_query_of_an_unknown_reference_stays_pending(self):
        """A 404/400 refuses the QUERY, not the product. It says nothing reliable
        about an already-submitted purchase, so refunding on it could double-spend."""
        for code in (400, 404, 422):
            with self.subTest(http_status=code):
                with mock.patch.object(wema, "_post", return_value=_response(
                        code, {"hasError": True, "message": "Record not found"})):
                    res = wema.vas_status("ZTCH-UNKNOWN", "airtime")
                self.assertTrue(res["pending"],
                                "a refused query must not refund a maybe-delivered buy")

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_legend_decode_still_wins_over_the_http_status(self):
        """A 200 carrying a decodable transactionStatus is a real answer and must be
        read as one — the refusal branch must not shadow the legend."""
        with mock.patch.object(wema, "_post", return_value=_response(
                200, {"hasError": False, "result": {"transactionStatus": 1}})):
            res = wema.vas_status("ZTCH-REF-5", "airtime")
        self.assertTrue(res["success"])
        self.assertFalse(res["pending"])


class RefusedMessageIsNotBlamedOnTheCustomerTests(TestCase):
    """"You've not been profiled" is about US. Handed to a customer verbatim it reads
    as their own account being ineligible, which is false and unactionable."""

    def test_the_customer_is_not_told_they_are_unprofiled(self):
        from wallet.services import PROVIDER_REFUSED_MESSAGE, customer_safe_failure

        with mock.patch("utility.alerts.alert"):
            msg = customer_safe_failure(
                {"message": "You've not been profiled to use this service"},
                service="airtime")
        self.assertEqual(msg, PROVIDER_REFUSED_MESSAGE)
        self.assertNotIn("profiled", msg)

    def test_an_entitlement_refusal_pages_someone(self):
        """No code change clears it — the tenant has to be provisioned for the
        product — and it fails every purchase silently until somebody does."""
        from wallet.services import customer_safe_failure

        with mock.patch("utility.alerts.alert") as alert:
            customer_safe_failure({"message": "Access denied"}, service="airtime")
        self.assertTrue(alert.called)

    def test_a_message_the_customer_can_act_on_still_reaches_them(self):
        from wallet.services import customer_safe_failure

        msg = customer_safe_failure({"message": "Invalid phone number for MTN"})
        self.assertEqual(msg, "Invalid phone number for MTN")


class AStuckRowActuallyClearsTests(TestCase):
    """End to end over the path the cron takes, because the whole point is the six
    rows sitting in production: requery → classify → settle_or_refund → money back."""

    def _stuck_airtime_row(self):
        from decimal import Decimal

        from wallet.models import Transaction
        from wallet.services import get_or_create_wallet
        from wallet.tests import make_user

        user, _ = make_user("08030000009", "stuck@zitch.test", balance="945")
        txn = Transaction.objects.create(
            user=user, amount=Decimal("55"), direction=Transaction.OUT,
            service="airtime", reference="ZTCH12083E287CEE",
            idempotency_key="wa-stuck-1", transaction_status=Transaction.PENDING,
            meta={"reconcile": True, "vas_rail": "wema", "vas_type": "airtime"})
        return user, txn, get_or_create_wallet(user)

    @override_settings(WEMA=WEMA_LIVE)
    def test_the_customer_gets_their_money_back(self):
        from wallet.models import Transaction
        from wallet.services import settle_or_refund
        from utility.providers import vas_requery

        user, txn, wallet = self._stuck_airtime_row()
        before = wallet.balance

        with mock.patch.object(wema, "_post", return_value=_response(403, NOT_PROFILED)):
            result = vas_requery(txn.reference, txn.meta)
        outcome = settle_or_refund(txn, result)

        self.assertEqual(outcome, "failed")
        txn.refresh_from_db()
        wallet.refresh_from_db()
        self.assertEqual(txn.transaction_status, Transaction.FAILED)
        self.assertEqual(wallet.balance, before + txn.amount)

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_second_pass_cannot_refund_twice(self):
        """The cron runs every ten minutes; the row must settle exactly once."""
        from wallet.services import settle_or_refund
        from utility.providers import vas_requery

        user, txn, wallet = self._stuck_airtime_row()
        before = wallet.balance

        with mock.patch.object(wema, "_post", return_value=_response(403, NOT_PROFILED)):
            for _ in range(3):
                settle_or_refund(txn, vas_requery(txn.reference, txn.meta))

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, before + txn.amount)
