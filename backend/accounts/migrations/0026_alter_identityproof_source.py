from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0025_identityproof")]

    operations = [
        migrations.AlterField(
            model_name="identityproof",
            name="source",
            field=models.CharField(
                choices=[
                    ("wema_wallet_otp", "Wema wallet OTP"),
                    ("wema_face", "Wema hosted face verification"),
                    ("wema_tier2", "Wema Tier 2 upgrade"),
                    ("identity_provider_otp", "Identity provider OTP"),
                ],
                max_length=32,
            ),
        ),
    ]
