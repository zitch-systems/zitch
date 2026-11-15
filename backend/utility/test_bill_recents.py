"""The bill screens' "Last used" defaults come from the customer's own payments.

Only successful bill payments count, newest first, one entry per identifier,
and never another customer's: a recents list is a list of someone's meters,
smartcards and phone numbers.
"""
from datetime import timedelta
from decimal import Decimal
from itertools import count
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import AccessToken, User
from betting.models import BettingPlatform
from wallet.models import Transaction

_refs = count(1)


def _user(n=1):
    phone = f"0803100{n:04d}"
    u = User.objects.create(username=phone, phone=phone, email=f"r{n}@example.test")
    u.set_unusable_password()
    u.save()
    return u


def _token(user):
    token = AccessToken.issue(user)
    return getattr(token, "raw", None) or getattr(token, "key", None)


class BillRecentsTests(TestCase):
    def setUp(self):
        self.user = _user()

    def _txn(self, service, meta, *, status=Transaction.SUCCESS, user=None, days_ago=0):
        # Ledger rows are immutable on PostgreSQL (a trigger refuses updates to
        # `created`), so an older row is written with the clock moved back.
        when = timezone.now() - timedelta(days=days_ago)
        with patch("django.utils.timezone.now", return_value=when):
            return Transaction.objects.create(
                user=user or self.user, amount=Decimal("1000"), direction=Transaction.OUT,
                service=service, reference=f"rec-{next(_refs)}", meta=meta, transaction_status=status)

    def _recents(self, user=None):
        r = self.client.post("/api/utility/recents/", {"access_token": _token(user or self.user)},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertTrue(body["success"])
        return body

    def test_phones_meters_smartcards_betting_and_exams_newest_first(self):
        self._txn("Airtime — MTN", {"phone": "08012345678", "network": "1"}, days_ago=3)
        self._txn("Data — GLO 2GB", {"phone": "+2348055519012", "network": "2"}, days_ago=2)
        self._txn("Airtime — MTN", {"phone": "08012345678", "network": "1"}, days_ago=1)
        self._txn("Electricity — Ikeja", {"meter": "01234567890", "disco": "1", "meter_type": "prepaid",
                                          "customer_name": "WILLIAM ADEYEMI", "customer_address": "12 Allen Avenue"})
        self._txn("Cable — GoTV Jinja", {"iuc": "7023451988", "provider": "1", "customer_name": "W ADEYEMI"})
        self._txn("Betting funding · Bet9ja", {"platform": "bet9ja", "user_id": "82314407"})
        self._txn("Exam PIN · WAEC x1", {"exam": "waec", "phone": "08099990000", "quantity": 1})

        body = self._recents()
        self.assertEqual([p["phone"] for p in body["phones"]], ["08012345678", "08055519012"])
        self.assertEqual(body["phones"][1]["network"], "2")
        self.assertEqual(body["meters"], [{
            "meter": "01234567890", "disco": "1", "meter_type": "prepaid", "name": "WILLIAM ADEYEMI",
            "address": "12 Allen Avenue", "last_used": body["meters"][0]["last_used"]}])
        self.assertEqual(body["smartcards"][0]["iuc"], "7023451988")
        self.assertEqual(body["smartcards"][0]["name"], "W ADEYEMI")
        self.assertEqual(body["betting"][0], {"platform": "bet9ja", "user_id": "82314407",
                                              "last_used": body["betting"][0]["last_used"]})
        self.assertEqual(body["exam_phones"][0]["phone"], "08099990000")

    def test_failed_pending_old_and_other_customers_payments_are_left_out(self):
        other = _user(2)
        self._txn("Airtime — MTN", {"phone": "08011110000", "network": "1"}, status=Transaction.FAILED)
        self._txn("Airtime — MTN", {"phone": "08011110001", "network": "1"}, status=Transaction.PENDING)
        self._txn("Airtime — MTN", {"phone": "08011110002", "network": "1"}, days_ago=400)
        self._txn("Airtime — MTN", {"phone": "08011110003", "network": "1"}, user=other)
        self._txn("transfer", {"phone": "08011110004", "network": "1"})
        body = self._recents()
        self.assertEqual(body["phones"], [])
        self.assertEqual([p["phone"] for p in self._recents(other)["phones"]], ["08011110003"])

    def test_malformed_meta_is_skipped(self):
        self._txn("Airtime — MTN", {"phone": "not a phone", "network": "1"})
        self._txn("Airtime — MTN", {"phone": "08012345678", "network": "9"})
        self._txn("Electricity — Ikeja", {"meter": "12", "disco": "1"})
        self._txn("Cable — DSTV", {"iuc": "7023451988", "provider": "nope"})
        self._txn("Betting funding · X", {"platform": "x", "user_id": "1"})
        body = self._recents()
        for kind in ("phones", "meters", "smartcards", "betting"):
            self.assertEqual(body[kind], [], kind)

    def test_signed_out_request_is_refused(self):
        r = self.client.post("/api/utility/recents/", {}, content_type="application/json")
        self.assertIn(r.status_code, (401, 403))


class BettingVerifyTests(TestCase):
    def setUp(self):
        self.user = _user(3)
        BettingPlatform.objects.create(code="bet9ja", name="Bet9ja", color="#0b3d2e", active=True)

    def _verify(self, **data):
        return self.client.post("/api/betting/verify/", {"access_token": _token(self.user), **data},
                                content_type="application/json")

    def test_name_on_the_betting_account(self):
        with patch("betting.views.vtu_verify_customer",
                   return_value={"success": True, "customer_name": "WILLIAM ADEYEMI"}) as verify:
            r = self._verify(platform="bet9ja", user_id="82314407")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["account_name"], "WILLIAM ADEYEMI")
        verify.assert_called_once_with("bet9ja-betting", "82314407")

    def test_unconfigured_platform_says_so_without_a_name(self):
        with patch("betting.views.vtu_verify_customer",
                   return_value={"success": False, "message": "This biller is not configured with our partner bank yet."}):
            r = self._verify(platform="bet9ja", user_id="82314407")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json().get("success"))

    def test_bad_input(self):
        self.assertEqual(self._verify(platform="nope", user_id="82314407").status_code, 404)
        self.assertEqual(self._verify(platform="bet9ja", user_id="12").status_code, 400)
