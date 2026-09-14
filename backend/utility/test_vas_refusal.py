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


class RefusedInTheBodyUnderHttp200Tests(SimpleTestCase):
    """The shape that actually reaches us, and that the HTTP-status check missed.

    The first version of this guard read only the status line, shipped, deployed —
    and all six stuck rows stayed exactly where they were, because ALAT answers an
    un-entitled product with HTTP *200* and the refusal in the body. Nine minutes
    after the deploy production was still logging wema_vas_requery_pending with no
    wema_vas_refused line anywhere.
    """

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_purchase_refused_in_the_body_is_a_definitive_failure(self):
        with mock.patch.object(wema, "_post", return_value=_response(200, NOT_PROFILED)):
            res = wema.purchase_airtime(55, "ZTCH-BODY-1", "07066737466", "MTN",
                                        source_account="0100000001")
        self.assertFalse(res["pending"], "a body-borne refusal must not be left pending")
        self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_the_requery_that_clears_the_six_stuck_rows(self):
        """Exactly what production returns for ZTCH12083E287CEE: HTTP 200, hasError,
        no status string, "You've not been profiled to use this service"."""
        with mock.patch.object(wema, "_post", return_value=_response(200, NOT_PROFILED)):
            res = wema.vas_status("ZTCH12083E287CEE", "airtime")
        self.assertFalse(res["pending"])
        self.assertFalse(res["success"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_authentication_failed_refunds_and_is_not_shown_to_the_customer(self):
        """Reported from production on a ₦55 top-up: the closing screen read
        "failed: Authentication Failed". Authentication is decided before the request
        is processed, so nothing was delivered and the debit must go back — and the
        customer must not be told their authentication failed, because it was ours."""
        from wallet.services import PROVIDER_REFUSED_MESSAGE, customer_safe_failure

        with mock.patch.object(wema, "_post", return_value=_response(
                200, {"hasError": True, "message": "Authentication Failed"})):
            res = wema.purchase_airtime(55, "ZTCH-AUTH-1", "07066737466", "MTN",
                                        source_account="0100000001")
        self.assertFalse(res["pending"], "an auth refusal must not be left pending")
        self.assertFalse(res["success"])

        with mock.patch("utility.alerts.alert"):
            shown = customer_safe_failure(res, service="airtime")
        self.assertEqual(shown, PROVIDER_REFUSED_MESSAGE)
        self.assertNotIn("Authentication", shown)

    @override_settings(WEMA=WEMA_LIVE)
    def test_the_other_refusal_wordings(self):
        for text in ("You are not subscribed to this service", "Access denied",
                     "Unauthorized", "Subscription key is invalid",
                     "Authentication Failed", "Invalid credentials"):
            with self.subTest(message=text):
                with mock.patch.object(wema, "_post", return_value=_response(
                        200, {"hasError": True, "message": text})):
                    res = wema.vas_status("ZTCH-W", "airtime")
                self.assertFalse(res["pending"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_SUCCESSFUL_envelope_is_never_refunded_on_wording_alone(self):
        """The error envelope is required as well as the wording. A delivered
        purchase whose message merely mentions authorisation must still settle."""
        with mock.patch.object(wema, "_post", return_value=_response(200, {
                "hasError": False,
                "result": {"status": "SUCCESS", "message": "authorized"}})):
            res = wema.purchase_airtime(55, "ZTCH-BODY-2", "07066737466", "MTN",
                                        source_account="0100000001")
        self.assertTrue(res["success"])
        self.assertFalse(res["pending"])

    @override_settings(WEMA=WEMA_LIVE)
    def test_an_ordinary_error_still_stays_pending(self):
        """Only wording that closes the PRODUCT refunds. A generic failure is still
        ambiguous about delivery, and refunding it could double-spend."""
        with mock.patch.object(wema, "_post", return_value=_response(
                200, {"hasError": True, "message": "Something went wrong"})):
            res = wema.vas_status("ZTCH-ORD", "airtime")
        self.assertTrue(res["pending"])


class RefusalLogCannotBeForgedTests(SimpleTestCase):
    """A reference reaches this log from outside the process. A newline inside one
    writes what reads as its own entry — and a forged "settled" line under a real
    reference is exactly what nobody would think to disbelieve while reading a
    settlement incident."""

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_newline_in_the_reference_cannot_open_a_second_log_line(self):
        forged = "ZTCH-1\nERROR zitch wema_vas_settled ref=ZTCH-1 (delivered)"
        with mock.patch.object(wema, "_post", return_value=_response(403, NOT_PROFILED)):
            with self.assertLogs("zitch", level="ERROR") as logs:
                wema.purchase_airtime(55, forged, "07066737466", "MTN",
                                      source_account="0100000001")
        for line in logs.output:
            self.assertNotIn("\n", line, "the reference forged a second log line")

    @override_settings(WEMA=WEMA_LIVE)
    def test_a_newline_in_the_gateway_message_cannot_either(self):
        payload = {"hasError": True,
                   "message": "refused\nERROR zitch everything is fine actually"}
        with mock.patch.object(wema, "_post", return_value=_response(403, payload)):
            with self.assertLogs("zitch", level="ERROR") as logs:
                wema.purchase_airtime(55, "ZTCH-REF-9", "07066737466", "MTN",
                                      source_account="0100000001")
        for line in logs.output:
            self.assertNotIn("\n", line)

    def test_an_oversized_value_cannot_drown_the_lines_around_it(self):
        self.assertLessEqual(len(wema._log_safe("Z" * 10_000)), 160)

    def test_an_ordinary_reference_is_untouched(self):
        self.assertEqual(wema._log_safe("ZTCH12083E287CEE"), "ZTCH12083E287CEE")

    def test_both_line_breaks_go(self):
        """Carriage return as well as newline — a lone \\r is enough to overwrite a
        rendered line in plenty of log viewers."""
        self.assertEqual(wema._log_safe("a\rb\nc"), "a b c")

    def test_a_gateway_status_code_is_sanitised_too(self):
        """transactionStatus is whatever JSON arrived, not the small integer the
        enum documents, and it is interpolated into the same log line."""
        self.assertNotIn("\n", wema._log_safe("1\nERROR forged"))


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
