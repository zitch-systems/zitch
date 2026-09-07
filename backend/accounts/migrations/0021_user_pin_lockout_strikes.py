"""Escalation counter for the transaction-PIN lockout.

Default 0 for every existing row, which is the honest backfill: the column is
"consecutive lockouts since the last correct PIN", and before this migration
nothing was counting. Seeding it any higher would put customers on the 24-hour
tier for a history the system never recorded.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_encrypt_totp_bind_sessions"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="pin_lockout_strikes",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
