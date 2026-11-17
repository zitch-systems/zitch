"""0040: no Tier 0. Every Tier 0 account moves to the tier its checks earn."""
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from accounts.models import User

lift = import_module("accounts.migrations.0040_tier_without_email").lift


class _Editor:
    connection = connection


class NoTierZeroMigrationTests(TestCase):
    def make(self, phone, **flags):
        fields = {"phone_verified": True, "email_verified": False, "tier": 0, **flags}
        user = User.objects.create(username=phone, phone=phone, **fields)
        User.objects.filter(pk=user.pk).update(tier=fields["tier"])
        return user

    def tier(self, user):
        user.refresh_from_db(fields=["tier"])
        return user.tier

    def test_every_tier_zero_account_reaches_at_least_tier_one(self):
        bvn = self.make("08010000001", bvn_verified=True)
        nin_face = self.make("08010000002", nin_verified=True, face_verified=True, tier_cap=3)
        held = self.make("08010000003", bvn_verified=True, face_verified=True, tier_cap=1)
        no_phone = self.make("08010000004", bvn_verified=True, phone_verified=False)
        no_identity = self.make("08010000005")
        lift(apps, _Editor())
        self.assertEqual(self.tier(bvn), 1)
        self.assertEqual(self.tier(nin_face), 2)
        self.assertEqual(self.tier(held), 1, "the sign-up hold still applies")
        self.assertEqual(self.tier(no_phone), 1)
        self.assertEqual(self.tier(no_identity), 1)
        self.assertFalse(User.objects.filter(tier=0).exists())

    def test_never_lowers_a_tier(self):
        user = self.make("08010000006", bvn_verified=True, tier=2, tier_cap=3)
        lift(apps, _Editor())
        self.assertEqual(self.tier(user), 2)

    def test_a_new_account_starts_at_tier_one(self):
        user = User.objects.create(username="08010000007", phone="08010000007")
        self.assertEqual(user.tier, 1)
        user.recompute_tier()
        self.assertEqual(user.tier, 1)
