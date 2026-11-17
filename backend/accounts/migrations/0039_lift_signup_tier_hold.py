"""Lift the sign-up hold (tier_cap) from customers who already have it.

Sign-up used to hold a customer on Tier 1 (tier_cap=1) until they tapped Verify
Tier 2, even when the bank had already confirmed it. Tiers now apply as soon as
the bank confirms them, so the customers still held get the tier their
verifications earn. The rule below mirrors User.recompute_tier at the time of
this migration. A tier is never lowered here.
"""
from django.db import migrations


def _earned(user):
    identity = user.bvn_verified or user.nin_verified
    if not (user.email_verified and user.phone_verified) or not identity:
        return 0
    if user.face_verified and user.address_verified:
        return 3
    if user.face_verified:
        return 2
    return 1


def lift_hold(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for user in User.objects.filter(tier_cap__lt=3).iterator():
        user.tier_cap = 3
        user.tier = max(user.tier, _earned(user))
        user.save(update_fields=["tier_cap", "tier"])


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_user_tier_cap_and_tier2_review"),
    ]

    operations = [
        migrations.RunPython(lift_hold, migrations.RunPython.noop),
    ]
