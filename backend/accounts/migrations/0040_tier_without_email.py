"""Tiers no longer need a verified email (17 Nov 2026): recompute the floor.

Customers who verified their phone and a BVN or NIN but never confirmed an
email were held at Tier 0. Lift exactly those to the tier their verifications
earn, under the same sign-up hold (tier_cap) recompute_tier applies. A tier is
never lowered here.
"""
from django.db import migrations


def lift(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    rows = User.objects.using(schema_editor.connection.alias).filter(
        tier=0, phone_verified=True, email_verified=False)
    for user in rows.iterator():
        if not (user.bvn_verified or user.nin_verified):
            continue
        if user.face_verified and user.address_verified:
            earned = 3
        elif user.face_verified:
            earned = 2
        else:
            earned = 1
        tier = min(earned, max(1, user.tier_cap))
        User.objects.using(schema_editor.connection.alias).filter(pk=user.pk, tier=0).update(tier=tier)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0039_lift_signup_tier_hold"),
    ]

    operations = [migrations.RunPython(lift, migrations.RunPython.noop)]
