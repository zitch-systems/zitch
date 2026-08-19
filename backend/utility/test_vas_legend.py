"""The VAS status legend — ALAT's undocumented integer `transactionStatus`.

The airtime/data status check returns an integer enum 1..11 and the bills one 1..9,
and Wema publishes neither legend. Everything here protects one property: a code we
cannot decode leaves the purchase PENDING. Settling an undelivered top-up debits a
customer for nothing; refunding a delivered one pays twice. So the legend is
configuration, it is parsed strictly, and every gap in it fails to PENDING.
"""
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings

from utility.wema import _parse_vas, _vas_legend, vas_status


def _int_shape(code):
    """The status-check envelope: no string status, only the integer."""
    return {"result": {"transactionStatus": code, "transactionReference": "REF1"},
            "hasError": False}


class VasLegendParsingTests(TestCase):
    def test_unset_legend_is_empty(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": ""}):
            self.assertEqual(_vas_legend("airtime"), {})

    def test_parses_comma_and_space_separated(self):
        with mock.patch.dict(settings.WEMA,
                             {"VAS_STATUS_LEGEND": "1=success, 2=pending  3=failed"}):
            self.assertEqual(_vas_legend("airtime"),
                             {"1": "success", "2": "pending", "3": "failed"})

    def test_airtime_and_bills_legends_are_separate_ladders(self):
        # The two endpoints publish different enums; decoding a bills code against the
        # airtime map would settle the wrong purchases.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "3=success",
                                             "BILLS_STATUS_LEGEND": "3=failed"}):
            self.assertEqual(_vas_legend("airtime")["3"], "success")
            self.assertEqual(_vas_legend("bills")["3"], "failed")

    def test_remita_has_its_own_ladder_and_never_borrows_airtime(self):
        # Remita is a separately-sold ALAT product with its own status enum. It used
        # to decode against the airtime legend (via _parse_vas's default argument),
        # so a deploy that buys Remita but not Airtime/Data — ours — had no legend at
        # all for its only Wema VAS product, and every integer-shaped Remita result
        # sat PENDING with the customer already debited.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "3=failed",
                                             "REMITA_STATUS_LEGEND": "3=success"}):
            self.assertEqual(_vas_legend("remita")["3"], "success")
            self.assertEqual(_vas_legend("airtime")["3"], "failed")

    def test_remita_with_no_legend_of_its_own_stays_pending(self):
        # Specifically: an airtime legend must not decide a Remita outcome.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "3=success",
                                             "REMITA_STATUS_LEGEND": ""}):
            out = _parse_vas(_int_shape(3), "REF1", product="remita")
        self.assertTrue(out["pending"])
        self.assertFalse(out["success"])

    def test_bad_entries_are_dropped_not_defaulted(self):
        with mock.patch.dict(settings.WEMA, {
                "VAS_STATUS_LEGEND": "1=success,2=maybe,three=failed,4,5=SUCCESS"}):
            legend = _vas_legend("airtime")
        # 2=maybe (not an outcome), three=failed (not a code) and bare 4 are dropped.
        self.assertEqual(legend, {"1": "success", "5": "success"})

    def test_outcome_is_case_insensitive(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "7=FAILED"}):
            self.assertEqual(_vas_legend("airtime"), {"7": "failed"})


class VasStatusDecodeTests(TestCase):
    @override_settings(DEBUG=False, TESTING=False,
                       WEMA={"CHANNEL_ID": "", "KEYS": {}, "SIMULATION": False})
    def test_unconfigured_production_requery_never_refunds_ambiguous_purchase(self):
        result = vas_status("REF1", "airtime")
        self.assertFalse(result["success"])
        self.assertTrue(result["pending"])

    def test_no_legend_leaves_the_purchase_pending(self):
        # Today's behaviour, and the behaviour that must survive every change here.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": ""}):
            res = _parse_vas(_int_shape(4), "REF1", product="airtime")
        self.assertFalse(res["success"])
        self.assertTrue(res["pending"])
        self.assertEqual(res["status"], "CODE_4")

    def test_code_outside_the_legend_stays_pending(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "1=success"}):
            res = _parse_vas(_int_shape(9), "REF1", product="airtime")
        self.assertFalse(res["success"])
        self.assertTrue(res["pending"])

    def test_mapped_success_settles(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "1=success"}):
            res = _parse_vas(_int_shape(1), "REF1", product="airtime")
        self.assertTrue(res["success"])
        self.assertFalse(res["pending"])

    def test_mapped_failed_refunds(self):
        # success False + pending False is what settle_or_refund reads as "refund".
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "3=failed"}):
            res = _parse_vas(_int_shape(3), "REF1", product="airtime")
        self.assertFalse(res["success"])
        self.assertFalse(res["pending"])

    def test_mapped_pending_stays_pending(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "2=pending"}):
            res = _parse_vas(_int_shape(2), "REF1", product="airtime")
        self.assertFalse(res["success"])
        self.assertTrue(res["pending"])

    def test_bills_uses_the_bills_legend(self):
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "1=success",
                                             "BILLS_STATUS_LEGEND": "1=failed"}):
            res = _parse_vas(_int_shape(1), "REF1", product="bills")
        self.assertFalse(res["success"])
        self.assertFalse(res["pending"])

    def test_string_status_shape_is_untouched_by_the_legend(self):
        # A purchase response carries a string status; the legend must not reach it.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "1=failed"}):
            res = _parse_vas({"result": {"status": "SUCCESS", "transactionStatus": 1},
                              "hasError": False}, "REF1", product="airtime")
        self.assertTrue(res["success"])
        self.assertEqual(res["status"], "SUCCESS")

    def test_a_typo_can_never_settle_an_unknown_code(self):
        # The whole point of strict parsing: "1=succes" is dropped, so code 1 keeps
        # failing safe instead of settling.
        with mock.patch.dict(settings.WEMA, {"VAS_STATUS_LEGEND": "1=succes"}):
            res = _parse_vas(_int_shape(1), "REF1", product="airtime")
        self.assertFalse(res["success"])
        self.assertTrue(res["pending"])
