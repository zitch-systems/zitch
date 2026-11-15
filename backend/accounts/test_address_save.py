"""The sign-up address is kept as given and never verifies anything.

POST /api/kyc/address/save/ stores the customer's address for the later Tier 3
check. It must not mark the address verified, must not change the tier, and must
not overwrite an address that is already verified or under review.
"""
from django.test import TestCase, override_settings

from accounts.models import AccessToken, User

BODY = {"house_number": "12", "street": "Allen  Avenue", "state": "lagos", "lga": "Ikeja"}


@override_settings(RATELIMIT_ENABLE=False)
class AddressSaveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="addr", phone="08022223333", email="addr@example.test", password="x",
            email_verified=True, phone_verified=True, bvn_verified=True, tier=1)
        token = AccessToken.issue(self.user)
        self.raw = getattr(token, "raw", None) or getattr(token, "key", None)

    def post(self, body):
        return self.client.post("/api/kyc/address/save/", {**body, "access_token": self.raw},
                                content_type="application/json")

    def test_saves_the_address_and_verifies_nothing(self):
        r = self.post(BODY)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()["success"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.address, "12 Allen Avenue, Ikeja, Lagos")
        self.assertFalse(self.user.address_verified)
        self.assertFalse(self.user.address_verification_pending)
        self.assertEqual(self.user.tier, 1)

    def test_needs_a_real_state_and_every_part(self):
        self.assertEqual(self.post({**BODY, "state": "Atlantis"}).status_code, 400)
        self.assertEqual(self.post({**BODY, "street": "  "}).status_code, 400)
        self.assertEqual(self.post({**BODY, "lga": ""}).status_code, 400)
        self.assertEqual(self.post({**BODY, "house_number": 12}).status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.address, "")

    def test_a_verified_or_pending_address_is_not_replaced(self):
        for field in ("address_verified", "address_verification_pending"):
            User.objects.filter(pk=self.user.pk).update(address="1 Old Road, Ikeja, Lagos",
                                                        address_verified=False, address_verification_pending=False)
            User.objects.filter(pk=self.user.pk).update(**{field: True})
            r = self.post(BODY)
            self.assertEqual(r.status_code, 409)
            self.user.refresh_from_db()
            self.assertEqual(self.user.address, "1 Old Road, Ikeja, Lagos")

    def test_needs_a_session(self):
        r = self.client.post("/api/kyc/address/save/", BODY, content_type="application/json")
        self.assertEqual(r.status_code, 401)
