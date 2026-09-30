from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0021_reversal_evidence")]
    operations = [
        migrations.AddField(
            model_name="wemaprovisioningattempt",
            name="otp_verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
