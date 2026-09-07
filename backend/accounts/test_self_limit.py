"""A customer may tighten their own transaction limit, never loosen it.

The tier ceiling is a KYC bound. A self-imposed limit is a guardrail against a
bad day or a stolen session, so it may only ever tighten what verification
already allows — which is why `transaction_limit` is a min() and emphatically not
a max(). Every test here exists to keep it that way: this is the number every
money path checks before moving anything.
"""
from decimal import Decimal

from django.test import TestCase

from accounts.models import AccessToken, User


_seq = iter(range(1, 999))


def _user(tier=2, pin="123456"):
    n = next(_seq)
    phone = f"0801000{n:04d}"
    u = User.objects.create(username=phone, phone=phone,
                            email=f"limits{n}@example.test", tier=tier,
                            email_verified=True, phone_verified=True)
    u.set_unusable_password()
    if pin:
        u.set_transaction_pin(pin)
    u.save()
    return u


class TransactionLimitPropertyTests(TestCase):

    def test_no_self_limit_means_the_tier_ceiling_applies(self):
        u = _user(tier=2)
        self.assertIsNone(u.self_txn_limit)
        self.assertEqual(u.transaction_limit, u.tier_transaction_limit)
        self.assertEqual(u.transaction_limit, Decimal("200000"))

    def test_a_lower_self_limit_wins(self):
        u = _user(tier=2)
        u.self_txn_limit = Decimal("25000")
        self.assertEqual(u.transaction_limit, Decimal("25000"))

    def test_a_self_limit_above_the_ceiling_can_never_raise_it(self):
        """Belt to the endpoint's braces. Even if a row were written directly —
        a shell, a fixture, a future migration — the ceiling still holds."""
        u = _user(tier=1)                      # ceiling 50,000
        u.self_txn_limit = Decimal("5000000")
        self.assertEqual(u.transaction_limit, Decimal("50000"))

    def test_a_stale_self_limit_does_not_survive_a_tier_drop(self):
        """Set at Tier 3, then demoted: bounded by the new tier, not the old choice."""
        u = _user(tier=3)
        u.self_txn_limit = Decimal("500000")
        self.assertEqual(u.transaction_limit, Decimal("500000"))
        u.tier = 1
        self.assertEqual(u.transaction_limit, Decimal("50000"))

    def test_zero_is_a_frozen_account_not_an_unset_one(self):
        u = _user(tier=2)
        u.self_txn_limit = Decimal("0")
        self.assertEqual(u.transaction_limit, Decimal("0"))


class SetTransactionLimitEndpointTests(TestCase):

    def setUp(self):
        self.user = _user(tier=2)

    def _post(self, path, **payload):
        # The token is issued fresh per call: AccessToken.issue returns the row and
        # the raw key is only available at creation on some builds, so the helper
        # keeps this test independent of which.
        token = AccessToken.issue(self.user)
        raw = getattr(token, "raw", None) or getattr(token, "key", None)
        return self.client.post(path, {"access_token": raw, **payload},
                                content_type="application/json")

    def test_lowering_the_limit_requires_the_pin(self):
        r = self._post("/api/limits/transaction/", limit="25000", pin="000000")
        self.assertEqual(r.status_code, 403)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_txn_limit)

    def test_lowering_the_limit_with_the_pin_works(self):
        r = self._post("/api/limits/transaction/", limit="25000", pin="123456")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.self_txn_limit, Decimal("25000"))
        self.assertEqual(self.user.transaction_limit, Decimal("25000"))

    def test_raising_above_the_tier_ceiling_is_refused_not_clamped(self):
        """Refused, because silently storing something other than what the
        customer typed — on the screen that tells them their limit — would be its
        own bug."""
        r = self._post("/api/limits/transaction/", limit="900000", pin="123456")
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("Tier 2", r.json().get("message", ""))
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_txn_limit)

    def test_the_ceiling_itself_is_allowed(self):
        r = self._post("/api/limits/transaction/", limit="200000", pin="123456")
        self.assertEqual(r.status_code, 200, r.content)

    def test_off_clears_the_self_limit(self):
        self.user.self_txn_limit = Decimal("1000")
        self.user.save(update_fields=["self_txn_limit"])
        r = self._post("/api/limits/transaction/", limit="off", pin="123456")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.self_txn_limit)
        self.assertEqual(self.user.transaction_limit, Decimal("200000"))

    def test_a_comma_formatted_amount_is_accepted(self):
        r = self._post("/api/limits/transaction/", limit="50,000", pin="123456")
        self.assertEqual(r.status_code, 200, r.content)
        self.user.refresh_from_db()
        self.assertEqual(self.user.self_txn_limit, Decimal("50000"))

    def test_nonsense_is_refused(self):
        for bad in ("abc", "1e9999", "--5"):
            r = self._post("/api/limits/transaction/", limit=bad, pin="123456")
            self.assertEqual(r.status_code, 400, f"{bad} -> {r.content}")

    def test_a_negative_limit_is_refused(self):
        r = self._post("/api/limits/transaction/", limit="-100", pin="123456")
        self.assertEqual(r.status_code, 400, r.content)

    def test_an_account_with_no_pin_is_told_to_set_one(self):
        u = _user(tier=1, pin="")
        token = AccessToken.issue(u)
        raw = getattr(token, "raw", None) or getattr(token, "key", None)
        r = self.client.post("/api/limits/transaction/",
                             {"access_token": raw, "limit": "1000", "pin": "123456"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get("code"), "no_pin")

    def test_reading_the_limits_reports_the_ceiling_separately(self):
        """The screen needs both: what applies now, and how high it could go."""
        self.user.self_txn_limit = Decimal("25000")
        self.user.save(update_fields=["self_txn_limit"])
        r = self._post("/api/limits/")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["transaction_limit"], "25000.00")
        self.assertEqual(body["tier_transaction_limit"], "200000")
        self.assertEqual(body["self_txn_limit"], "25000.00")

    def test_an_unset_self_limit_reads_as_null_not_zero(self):
        r = self._post("/api/limits/")
        self.assertIsNone(r.json()["self_txn_limit"])
