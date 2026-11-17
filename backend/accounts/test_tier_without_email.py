"""0040: customers held at Tier 0 only by an unconfirmed email are lifted once."""
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from accounts.models import User

lift = import_module("accounts.migrations.0040_tier_without_email").lift


class _Editor:
    connection = connection


class TierWithoutEmailMigrationTests(TestCase):
    def make(self, phone, **flags):
        fields = {"phone_verified": True, "email_verified": False, "tier": 0, **flags}
        user = User.objects.create(username=phone, phone=phone, **fields)
        User.objects.filter(pk=user.pk).update(tier=fields["tier"])
        return user

    def tier(self, user):
        user.refresh_from_db(fields=["tier"])
        return user.tier

    def test_lifts_exactly_the_customers_the_email_held_back(self):
        bvn = self.make("08010000001", bvn_verified=True)
        nin_face = self.make("08010000002", nin_verified=True, face_verified=True, tier_cap=3)
        held = self.make("08010000003", bvn_verified=True, face_verified=True, tier_cap=1)
        no_phone = self.make("08010000004", bvn_verified=True, phone_verified=False)
        no_identity = self.make("08010000005")
        lift(apps, _Editor())
        self.assertEqual(self.tier(bvn), 1)
        self.assertEqual(self.tier(nin_face), 2)
        self.assertEqual(self.tier(held), 1, "the sign-up hold still applies")
        self.assertEqual(self.tier(no_phone), 0)
        self.assertEqual(self.tier(no_identity), 0)

    def test_never_lowers_a_tier(self):
        user = self.make("08010000006", bvn_verified=True, tier=2, tier_cap=3)
        lift(apps, _Editor())
        self.assertEqual(self.tier(user), 2)
