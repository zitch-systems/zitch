from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0031_user_address_verification_pending")]

    operations = [
        migrations.AddField(
            model_name="accesstoken",
            name="session_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="pushdevice",
            name="session_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
