"""Per-tier daily aggregate limits.

Transfers are capped at the user's `daily_transfer_limit` and bills at
`daily_bill_limit` per day, on top of the per-transaction limit. The caps live
on the user (per KYC tier), so they apply identically in the app and on
WhatsApp. WhatsApp onboarding (BVN -> Tier 2) caps at ₦1,000,000 transfers /
₦100,000 bills a day; full app KYC (Tier 3) raises them.
"""
import json
from decimal import Decimal

from django.test import Client, TestCase

from common.http import daily_limit_error

from .models import Transaction
from .tests import make_user


def _seed_out(user, service, amount):
    """A prior same-day outbound ledger row (counts toward the daily total)."""
    return Transaction.objects.create(
        user=user, service=service, amount=Decimal(amount),
        direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
        reference=f"SEED-{user.id}-{service}-{amount}",
    )


class DailyLimitModelTests(TestCase):
    def test_tier_caps(self):
        u2, _ = make_user("08070000001", "d2@zitch.test", tier=2)
        self.assertEqual(u2.daily_transfer_limit, Decimal("1000000"))
        self.assertEqual(u2.daily_bill_limit, Decimal("100000"))
        u3, _ = make_user("08070000002", "d3@zitch.test", tier=3)
        self.assertEqual(u3.daily_transfer_limit, Decimal("5000000"))
        self.assertEqual(u3.daily_bill_limit, Decimal("500000"))


class DailyLimitHelperTests(TestCase):
    def test_transfer_daily_cap(self):
        u, _ = make_user("08070000010", "dt@zitch.test", tier=2)
        _seed_out(u, "Transfer to Ada", "900000")
        # 900k + 50k = 950k <= 1M -> allowed
        self.assertIsNone(daily_limit_error(u, Decimal("50000"), "transfer"))
        # 900k + 200k = 1.1M > 1M -> blocked
        self.assertIsNotNone(daily_limit_error(u, Decimal("200000"), "transfer"))

    def test_bill_daily_cap(self):
        u, _ = make_user("08070000011", "db@zitch.test", tier=2)
        _seed_out(u, "Airtime — MTN", "90000")
        self.assertIsNone(daily_limit_error(u, Decimal("5000"), "bill"))
        self.assertIsNotNone(daily_limit_error(u, Decimal("20000"), "bill"))

    def test_categories_are_separate(self):
        # A day of transfers must not eat into the bill allowance.
        u, _ = make_user("08070000013", "dc@zitch.test", tier=2)
        _seed_out(u, "Transfer to Ada", "1000000")  # transfer cap fully used
        self.assertIsNone(daily_limit_error(u, Decimal("50000"), "bill"))
        self.assertIsNotNone(daily_limit_error(u, Decimal("1"), "transfer"))

    def test_failed_txns_dont_count(self):
        u, _ = make_user("08070000012", "df@zitch.test", tier=2)
        t = _seed_out(u, "Transfer to X", "900000")
        Transaction.objects.filter(pk=t.pk).update(transaction_status=Transaction.FAILED)
        # the failed 900k is excluded, so a 200k transfer is fine again
        self.assertIsNone(daily_limit_error(u, Decimal("200000"), "transfer"))


class DailyLimitEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.sender, self.token = make_user("08070000020", "ds@zitch.test", balance="3000000", tier=2)
        self.sender.face_verified = True  # bypass the >=₦100k face gate for the test
        self.sender.save(update_fields=["face_verified"])
        self.recipient, _ = make_user("08070000021", "dr@zitch.test", tier=2)

    def post(self, path, payload):
        return self.client.post(
            path, data=json.dumps({**payload, "access_token": self.token}),
            content_type="application/json",
        )

    def test_transfer_blocked_over_daily_cap(self):
        _seed_out(self.sender, "Transfer to Seed", "900000")
        res = self.post("/api/transfer/send/", {
            "identifier": self.recipient.phone, "amount": "150000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json().get("code"), "daily_limit_exceeded")

    def test_transfer_allowed_under_daily_cap(self):
        _seed_out(self.sender, "Transfer to Seed", "800000")
        res = self.post("/api/transfer/send/", {
            "identifier": self.recipient.phone, "amount": "50000", "transaction_pin": "1234",
        })
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))
