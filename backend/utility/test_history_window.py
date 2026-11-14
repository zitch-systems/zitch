"""The statement must cover the whole of its `to` day.

The bank types `to` as a date-time and returns rows up to that instant. A bare
date is the first instant of its day, so a statement asked "to today" stopped at
last midnight and a deposit was not found until the next day (production,
13-14 November 2026: the bank's balance held a deposit that no statement read
returned). These tests pin the request that includes today, and that a bank
which turns the wider spelling down leaves things no worse than before.
"""
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from utility import wema
from utility.test_wema import WEMA_LIVE, _resp

ROW = {"referenceId": "338135484403", "tranId": "T1", "rrn": "338135484403",
       "amount": 500, "balance": 2468, "creditType": "Credit", "status": "Default",
       "date": "2026-11-14"}
OK = {"successful": True, "message": "Successful!", "result": [ROW]}
REFUSED = {"successful": False, "message": "Invalid date range"}


@override_settings(WEMA=WEMA_LIVE)
class StatementWindowTests(SimpleTestCase):
    def setUp(self):
        for key in (wema._HISTORY_TO_FORM_KEY, wema._HISTORY_TO_PROVEN_KEY):
            cache.delete(key)
            self.addCleanup(cache.delete, key)

    def _asked(self, post):
        return [call.kwargs["json"]["to"] for call in post.call_args_list]

    def test_the_statement_is_asked_through_the_end_of_today(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "next-day")
        # One request, and it reaches past today's last transaction.
        self.assertEqual(self._asked(post), ["2026-11-15"])
        self.assertEqual(post.call_args.kwargs["json"]["from"], "2026-11-13")
        self.assertEqual(result["transactions"], [ROW])

    def test_a_month_or_year_end_rolls_over(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-12-01", "2026-12-31")
        self.assertEqual(self._asked(post), ["2027-01-01"])

    def test_a_refused_spelling_falls_back_and_is_skipped_afterwards(self):
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(REFUSED, 400), _resp(OK)]) as post:
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "end-of-day")
        self.assertEqual(self._asked(post), ["2026-11-15", "2026-11-14T23:59:59"])
        # The next read does not ask the refused way again.
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertEqual(self._asked(post), ["2026-11-14T23:59:59"])

    def test_the_bare_date_is_the_last_resort(self):
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(REFUSED, 400), _resp(REFUSED, 422), _resp(OK)]) as post:
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "plain")
        self.assertEqual(self._asked(post),
                         ["2026-11-15", "2026-11-14T23:59:59", "2026-11-14"])

    def test_a_refusal_of_everything_remembers_nothing(self):
        with patch("utility.wema.requests.post", return_value=_resp(REFUSED, 400)) as post:
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertFalse(result["success"])
        self.assertEqual(len(post.call_args_list), 3)
        self.assertIsNone(cache.get(wema._HISTORY_TO_FORM_KEY))

    def test_an_outage_or_a_sign_in_failure_is_not_retried_another_way(self):
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status):
                with patch("utility.wema.requests.post",
                           return_value=_resp({"successful": False}, status)) as post:
                    result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
                self.assertFalse(result["success"])
                self.assertEqual(len(post.call_args_list), 1)

    EMPTY = {"message": "No record found."}
    BALANCE = {"successful": True, "result": {"availableBalance": "100.00"}}

    def test_an_empty_statement_is_an_answer_not_a_refusal(self):
        # Once the wide spelling has returned rows for any account, "nothing" from
        # it is taken as the answer: one request, no second opinion.
        with patch("utility.wema.requests.post", return_value=_resp(OK)):
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        with patch("utility.wema.requests.post",
                   return_value=_resp(self.EMPTY, 400)) as post, \
                patch("utility.wema.requests.get", return_value=_resp(self.BALANCE)):
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertTrue(result["success"])
        self.assertTrue(result["empty"])
        self.assertEqual(self._asked(post), ["2026-11-15"])

    def test_an_unproven_spelling_that_says_nothing_is_checked_against_the_plain_date(self):
        # The wide request has never returned a row and now says "nothing", but the
        # plain date has rows: the bank is mishandling the spelling. Keep the rows.
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(self.EMPTY, 400), _resp(OK)]) as post, \
                patch("utility.wema.requests.get", return_value=_resp(self.BALANCE)):
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertEqual(self._asked(post), ["2026-11-15", "2026-11-14"])
        self.assertEqual(result["to_form"], "plain")
        self.assertEqual(result["transactions"], [ROW])
        # And the plain date is used for the next hour.
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertEqual(self._asked(post), ["2026-11-14"])

    def test_an_account_that_really_is_empty_stays_empty(self):
        with patch("utility.wema.requests.post",
                   return_value=_resp(self.EMPTY, 400)) as post, \
                patch("utility.wema.requests.get", return_value=_resp(self.BALANCE)):
            result = wema.get_transactions("0458386712", "2026-11-13", "2026-11-14")
        self.assertTrue(result["success"])
        self.assertTrue(result["empty"])
        self.assertEqual(result["to_form"], "next-day")
        self.assertEqual(self._asked(post), ["2026-11-15", "2026-11-14"])

    def test_an_instant_the_caller_chose_is_sent_as_given(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-11-13", "2026-11-14T10:00:00")
        self.assertEqual(self._asked(post), ["2026-11-14T10:00:00"])


class WindowDigestTests(SimpleTestCase):
    def test_the_digest_carries_counts_and_dates_only(self):
        from wallet.reconciliation import history_window_digest

        rows = [ROW, {"creditType": "Debit", "amount": 53, "date": "2026-11-13",
                      "recieverName": "SOMEONE", "narration": "Transfer to SOMEONE"}]
        digest = history_window_digest({"to_form": "next-day"}, rows)
        self.assertEqual(digest, {"to_form": "next-day", "rows": 2, "credits": 1,
                                  "newest": date(2026, 11, 14).isoformat()})
        self.assertNotIn("SOMEONE", str(digest))

    def test_an_empty_window_still_reports(self):
        from wallet.reconciliation import history_window_digest

        self.assertEqual(history_window_digest({}, []),
                         {"to_form": "as-given", "rows": 0, "credits": 0, "newest": "none"})
