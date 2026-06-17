from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0006_transaction_txn_user_created_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="account_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="wallet",
            name="bank_name",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="wallet",
            name="account_reference",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="wallet",
            name="bank_accounts",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
