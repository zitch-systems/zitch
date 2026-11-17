"""No Tier 0, and no tier needs a verified email (17 Nov 2026).

Every account is at least Tier 1. Move each Tier 0 account to the tier its
verified checks earn, under the same sign-up hold (tier_cap) recompute_tier
applies, and never lower than Tier 1. A tier above 0 is never touched.

The AlterField only changes Django's Python-side default (0 -> 1); Django does
not put field defaults in the database, so no column or table is rewritten.
"""
from django.db import migrations, models


def lift(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    rows = User.objects.using(schema_editor.connection.alias).filter(tier=0)
    for user in rows.iterator():
        earned = 1
        if user.phone_verified and (user.bvn_verified or user.nin_verified):
            if user.face_verified and user.address_verified:
                earned = 3
            elif user.face_verified:
                earned = 2
        tier = min(earned, max(1, user.tier_cap))
        User.objects.using(schema_editor.connection.alias).filter(pk=user.pk, tier=0).update(tier=tier)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0039_lift_signup_tier_hold"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="tier",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.RunPython(lift, migrations.RunPython.noop),
    ]
