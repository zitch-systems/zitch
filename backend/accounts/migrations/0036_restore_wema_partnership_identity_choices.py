"""Forward-only source-choice state reconciliation for restored Wema Partnership.

The existing 9PSB-era identity proof rows are immutable historical evidence and
are not deleted or reassigned by this migration. Only the active Django choices
for *new* identity proofs return to the Wema Partnership contract.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0035_tier2_nin_upgrade"),
    ]

    operations = [
        migrations.AlterField(
            model_name="identityproof",
            name="source",
            field=models.CharField(
                max_length=32,
                choices=[
                    ("wema_wallet_otp", "Wema wallet OTP"),
                    ("wema_face", "Wema hosted face verification"),
                    ("wema_tier2", "Wema Tier 2 upgrade"),
                    ("identity_provider_otp", "Identity provider OTP"),
                ],
            ),
        ),
    ]
