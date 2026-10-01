"""History reads must never acknowledge a malformed or partial statement."""
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from utility import wema
from utility.test_wema import WEMA_LIVE, _resp


@override_settings(WEMA=WEMA_LIVE)
class HistoryResponseContractTests(SimpleTestCase):
    def test_non_finite_bank_amounts_are_unparseable(self):
        for value in ("NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                self.assertIsNone(wema._naira(value))

    def history(self, body, status=200):
        with patch("utility.wema.requests.post", return_value=_resp(body, status)):
            return wema.get_transactions("0452491368", "2026-09-01", "2026-09-07")

    def test_documented_array_and_explicit_empty_are_complete(self):
        for rows in ([], [{"referenceId": "R1", "date": "2026-09-01", "amount": 500}]):
            with self.subTest(rows=rows):
                result = self.history({"successful": True, "result": rows})
                self.assertTrue(result["success"])
                self.assertTrue(result["complete"])
                self.assertEqual(result["transactions"], rows)

    def test_existing_nested_deployment_envelopes_remain_supported(self):
        for key in ("result", "transactions", "data"):
            with self.subTest(key=key):
                result = self.history({"successful": True, "data": {key: []}})
                self.assertTrue(result["success"])
                self.assertTrue(result["complete"])

    def test_missing_or_wrong_result_is_failure_not_empty_success(self):
        for body in ({"successful": True}, {"successful": True, "result": None},
                     {"successful": True, "result": "unavailable"},
                     {"successful": True, "result": {}},
                     {"successful": True, "data": {"unrecognized": []}},
                     {"successful": True, "result": [None]},
                     {"successful": True, "result": ["row"]}):
            with self.subTest(body=body):
                result = self.history(body)
                self.assertFalse(result["success"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["error_code"], "malformed_history")

    def test_pagination_and_truncation_hints_are_fail_closed(self):
        for hints in ({"hasMore": True}, {"hasNextPage": "true"},
                      {"nextCursor": "page-2"}, {"nextLink": "/next"},
                      {"truncated": True}, {"totalCount": 2},
                      {"pagination": {"totalPages": 2}}, {"complete": False}):
            with self.subTest(hints=hints):
                result = self.history({"successful": True, "result": [{"referenceId": "R1"}], **hints})
                self.assertFalse(result["success"])
                self.assertFalse(result["complete"])
                self.assertEqual(result["error_code"], "incomplete_history")

    def test_total_for_complete_result_is_supported(self):
        result = self.history({"successful": True, "result": [], "totalCount": 0,
                               "pagination": {"totalPages": 1, "hasMore": False}})
        self.assertTrue(result["success"])

    def test_documented_no_record_found_remains_empty_success(self):
        result = self.history({"successful": False, "message": "No record found"}, status=400)
        self.assertTrue(result["success"])
        self.assertTrue(result["complete"])
        self.assertEqual(result["transactions"], [])

    def test_invalid_json_is_failure(self):
        response = _resp({})
        response.json.side_effect = ValueError("invalid JSON")
        with patch("utility.wema.requests.post", return_value=response):
            result = wema.get_transactions("0452491368", "2026-09-01", "2026-09-07")
        self.assertFalse(result["success"])
        self.assertFalse(result["complete"])
