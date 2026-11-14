"""Which rows a statement holds depends on how its `to` is written.

The bank types `to` as a date-time and does not document how it is applied. In
production (13-14 November 2026) a bare date stopped at the previous midnight,
and tomorrow's date was accepted yet still ended on yesterday's rows while the
bank's balance already held that morning's deposit. So no spelling is assumed:
each is tried, the one that reaches furthest is remembered, and the bare date
stays as the last resort. These tests pin that behaviour.
"""
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from utility import wema
from utility.test_wema import WEMA_LIVE, _resp

ROW = {"referenceId": "338135484403", "tranId": "T1", "rrn": "338135484403",
       "amount": 500, "balance": 2468, "creditType": "Credit", "status": "Default",
       "date": "2026-07-20T00:10:00"}
OLD_ROW = {**ROW, "referenceId": "338135480000", "rrn": "338135480000",
           "date": "2026-07-19T18:52:00"}
OK = {"successful": True, "message": "Successful!", "result": [ROW]}
STALE = {"successful": True, "message": "Successful!", "result": [OLD_ROW]}
REFUSED = {"successful": False, "message": "Invalid date range"}
EMPTY = {"message": "No record found."}
BALANCE = {"successful": True, "result": {"availableBalance": "100.00"}}
LAGOS = ZoneInfo("Africa/Lagos")
# 03:31:07 in Lagos on the day being asked for: "today" for the two `now` spellings.
NOW = datetime(2026, 7, 20, 3, 31, 7, tzinfo=LAGOS)
OTHER_DAY = datetime(2026, 9, 1, 12, 0, 0, tzinfo=LAGOS)


class _Base(SimpleTestCase):
    now = OTHER_DAY

    def setUp(self):
        for key in (wema._HISTORY_TO_FORM_KEY, wema._HISTORY_TO_PROVEN_KEY):
            cache.delete(key)
            self.addCleanup(cache.delete, key)
        clock = patch("django.utils.timezone.localtime", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def _asked(self, post):
        return [call.kwargs["json"]["to"] for call in post.call_args_list]


@override_settings(WEMA=WEMA_LIVE)
class StatementWindowTests(_Base):
    """A day that is not today: next day, end of day, then the bare date."""

    def test_the_statement_is_asked_past_the_end_of_the_day(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "next-day")
        self.assertEqual(self._asked(post), ["2026-07-21"])
        self.assertEqual(post.call_args.kwargs["json"]["from"], "2026-07-19")
        self.assertEqual(result["transactions"], [ROW])

    def test_a_month_or_year_end_rolls_over(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-12-01", "2026-12-31")
        self.assertEqual(self._asked(post), ["2027-01-01"])

    def test_a_refused_spelling_falls_back_and_the_working_one_goes_first_afterwards(self):
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(REFUSED, 400), _resp(OK)]) as post:
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "end-of-day")
        self.assertEqual(self._asked(post), ["2026-07-21", "2026-07-20T23:59:59"])
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual(self._asked(post), ["2026-07-20T23:59:59"])

    def test_the_bare_date_is_the_last_resort(self):
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(REFUSED, 400), _resp(REFUSED, 422), _resp(OK)]) as post:
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(result["success"])
        self.assertEqual(result["to_form"], "plain")
        self.assertEqual(self._asked(post),
                         ["2026-07-21", "2026-07-20T23:59:59", "2026-07-20"])

    def test_a_refusal_of_everything_remembers_nothing(self):
        with patch("utility.wema.requests.post", return_value=_resp(REFUSED, 400)) as post:
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertFalse(result["success"])
        self.assertEqual(len(post.call_args_list), 3)
        self.assertIsNone(cache.get(wema._HISTORY_TO_FORM_KEY))

    def test_an_outage_or_a_sign_in_failure_is_not_retried_another_way(self):
        for status in (401, 403, 429, 500, 503):
            with self.subTest(status=status):
                with patch("utility.wema.requests.post",
                           return_value=_resp({"successful": False}, status)) as post:
                    result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
                self.assertFalse(result["success"])
                self.assertEqual(len(post.call_args_list), 1)

    def test_an_empty_statement_is_an_answer_not_a_refusal(self):
        # Once a wide spelling has returned rows for any account, "nothing" from
        # it is taken as the answer: one request, no second opinion.
        with patch("utility.wema.requests.post", return_value=_resp(OK)):
            wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        with patch("utility.wema.requests.post", return_value=_resp(EMPTY, 400)) as post, \
                patch("utility.wema.requests.get", return_value=_resp(BALANCE)):
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(result["success"])
        self.assertTrue(result["empty"])
        self.assertEqual(self._asked(post), ["2026-07-21"])

    def test_an_unproven_spelling_that_says_nothing_is_checked_against_the_plain_date(self):
        # The wide request has never returned a row and now says "nothing", but the
        # plain date has rows: the bank is mishandling the spelling. Keep the rows.
        with patch("utility.wema.requests.post",
                   side_effect=[_resp(EMPTY, 400), _resp(OK)]) as post, \
                patch("utility.wema.requests.get", return_value=_resp(BALANCE)):
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual(self._asked(post), ["2026-07-21", "2026-07-20"])
        self.assertEqual(result["to_form"], "plain")
        self.assertEqual(result["transactions"], [ROW])
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual(self._asked(post), ["2026-07-20"])

    def test_an_account_that_really_is_empty_stays_empty(self):
        with patch("utility.wema.requests.post", return_value=_resp(EMPTY, 400)) as post, \
                patch("utility.wema.requests.get", return_value=_resp(BALANCE)):
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(result["success"])
        self.assertTrue(result["empty"])
        self.assertEqual(result["to_form"], "next-day")
        self.assertEqual(self._asked(post), ["2026-07-21", "2026-07-20"])

    def test_an_instant_the_caller_chose_is_sent_as_given(self):
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            wema.get_transactions("0458386712", "2026-07-19", "2026-07-20T10:00:00")
        self.assertEqual(self._asked(post), ["2026-07-20T10:00:00"])


@override_settings(WEMA=WEMA_LIVE)
class TodayTests(_Base):
    """Today can also be asked for "up to now", in Lagos time and in UTC."""
    now = NOW

    def test_today_adds_the_two_now_spellings(self):
        self.assertEqual(wema._history_to_values("2026-07-20"), [
            ("next-day", "2026-07-21"),
            ("end-of-day", "2026-07-20T23:59:59"),
            ("now", "2026-07-20T03:31:07"),
            ("now-utc", "2026-07-20T02:31:07Z"),
            ("plain", "2026-07-20"),
        ])

    def test_another_day_does_not(self):
        self.assertEqual([name for name, _ in wema._history_to_values("2026-07-19")],
                         ["next-day", "end-of-day", "plain"])

    def test_the_probe_prefers_the_spelling_that_reaches_furthest(self):
        # Tomorrow's date and end-of-day still end on yesterday's row; "now" in
        # Lagos time returns this morning's deposit. That one is used from then on.
        answers = [_resp(STALE), _resp(STALE), _resp(OK), _resp(REFUSED, 400), _resp(STALE)]
        with patch("utility.wema.requests.post", side_effect=answers) as post:
            found = wema.probe_history_to_forms("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual(self._asked(post), [
            "2026-07-21", "2026-07-20T23:59:59", "2026-07-20T03:31:07",
            "2026-07-20T02:31:07Z", "2026-07-20"])
        self.assertEqual([(e["form"], e["ok"], e["rows"], e["newest"]) for e in found], [
            ("next-day", True, 1, "2026-07-19T18:52:00"),
            ("end-of-day", True, 1, "2026-07-19T18:52:00"),
            ("now", True, 1, "2026-07-20T00:10:00"),
            ("now-utc", False, 0, ""),
            ("plain", True, 1, "2026-07-19T18:52:00"),
        ])
        self.assertEqual([e["form"] for e in found if e.get("chosen")], ["now"])
        with patch("utility.wema.requests.post", return_value=_resp(OK)) as post:
            result = wema.get_transactions("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual(self._asked(post), ["2026-07-20T03:31:07"])
        self.assertEqual(result["to_form"], "now")

    def test_when_every_spelling_ends_on_the_same_row_the_first_is_kept(self):
        with patch("utility.wema.requests.post", return_value=_resp(STALE)):
            found = wema.probe_history_to_forms("0458386712", "2026-07-19", "2026-07-20")
        self.assertEqual([e["form"] for e in found if e.get("chosen")], ["next-day"])
        self.assertEqual(cache.get(wema._HISTORY_TO_FORM_KEY), "next-day")

    def test_the_probe_reports_no_customer_data(self):
        row = {**ROW, "sender": "SOMEONE", "narration": "Transfer from SOMEONE",
               "senderAccountNumber": "0123456789"}
        with patch("utility.wema.requests.post",
                   return_value=_resp({"successful": True, "result": [row]})):
            found = wema.probe_history_to_forms("0458386712", "2026-07-19", "2026-07-20")
        text = str(found)
        for secret in ("SOMEONE", "0123456789", "0458386712", "338135484403"):
            self.assertNotIn(secret, text)

    def test_a_probe_that_gets_no_rows_remembers_nothing(self):
        with patch("utility.wema.requests.post", return_value=_resp(REFUSED, 500)):
            found = wema.probe_history_to_forms("0458386712", "2026-07-19", "2026-07-20")
        self.assertTrue(all(not e["ok"] for e in found))
        self.assertIsNone(cache.get(wema._HISTORY_TO_FORM_KEY))


class WindowDigestTests(SimpleTestCase):
    def test_the_digest_carries_counts_and_dates_only(self):
        from wallet.reconciliation import history_window_digest

        rows = [ROW, {"creditType": "Debit", "amount": 53, "date": "2026-07-19",
                      "recieverName": "SOMEONE", "narration": "Transfer to SOMEONE"}]
        digest = history_window_digest({"to_form": "next-day"}, rows)
        self.assertEqual(digest, {"to_form": "next-day", "rows": 2, "credits": 1,
                                  "newest": date(2026, 7, 20).isoformat()})
        self.assertNotIn("SOMEONE", str(digest))

    def test_an_empty_window_still_reports(self):
        from wallet.reconciliation import history_window_digest

        self.assertEqual(history_window_digest({}, []),
                         {"to_form": "as-given", "rows": 0, "credits": 0, "newest": "none"})
