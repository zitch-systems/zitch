import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0023_transactionalertdelivery")]

    operations = [
        migrations.CreateModel(
            name="BankHistoryCheckpoint",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_number", models.CharField(max_length=20)),
                ("opening_review_required", models.BooleanField(default=True)),
                ("covered_through", models.DateField(blank=True, null=True)),
                ("last_completed_at", models.DateTimeField(blank=True, null=True)),
                ("last_error_code", models.CharField(blank=True, default="", max_length=64)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("wallet", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bank_history_checkpoints", to="wallet.wallet")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("wallet", "account_number"), name="uniq_wallet_history_account")]},
        ),
    ]
