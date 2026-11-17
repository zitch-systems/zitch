"""Kept as a no-op: the sign-up hold is Zitch's own limiter again.

#735 shipped this migration to lift the sign-up hold (tier_cap) from customers
who still had it, when tiers were to apply as soon as the bank confirmed them.
The same day (17 Nov 2026) that was reversed: a tier applies only when the
customer taps it. So this does nothing now. Where it already ran, the customers
it lifted keep their tier; it is never lowered. The file stays so the migration
history is the same everywhere.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_user_tier_cap_and_tier2_review"),
    ]

    operations = []
