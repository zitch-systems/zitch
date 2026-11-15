"""The app's tier ladder reads every tier's caps from the server, not a copy."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import AccessToken


class TierLadderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="ladder", phone="08038889911", email="ladder@example.test",
            password="Test-only-password1!", email_verified=True, phone_verified=True,
            address="12 Allen Avenue, Ikeja, Lagos",
        )

    def test_status_carries_each_tiers_limits_and_the_customers_address(self):
        token = AccessToken.issue(self.user)
        raw = getattr(token, "raw", None) or getattr(token, "key", None)
        r = self.client.post("/api/kyc/status/", {"access_token": raw}, content_type="application/json")
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body["tier_ladder"], [
            {"tier": 1, "name": "Verified", "per_transfer": "50000", "daily_transfer": "50000", "daily_bill": "20000"},
            {"tier": 2, "name": "Enhanced", "per_transfer": "200000", "daily_transfer": "1000000", "daily_bill": "100000"},
            {"tier": 3, "name": "Premium", "per_transfer": "5000000", "daily_transfer": "5000000", "daily_bill": "500000"},
        ])
        self.assertEqual(body["address"], "12 Allen Avenue, Ikeja, Lagos")
        # giving an address is not verifying it
        self.assertFalse(body["address_verified"])
