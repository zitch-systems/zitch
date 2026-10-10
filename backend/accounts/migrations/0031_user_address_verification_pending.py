from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0030_identityproof_verified_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="address_verification_pending",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="user",
            name="address_verification_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="address_verification_account_number",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
    ]
