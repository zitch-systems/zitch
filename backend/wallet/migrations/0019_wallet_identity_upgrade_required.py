from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0018_wallet_pnd_lifted")]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="identity_upgrade_required",
            field=models.BooleanField(default=False),
        ),
    ]
