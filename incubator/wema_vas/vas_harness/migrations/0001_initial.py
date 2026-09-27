import uuid
from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="VirtualAccount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.CharField(max_length=10, unique=True)),
                ("active", models.BooleanField(default=True)),
                ("simulated_balance", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("block_reason", models.CharField(blank=True, max_length=200)),
                ("blocked_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(simulated_balance__gte=0), name="vas_sim_balance_nonnegative")]},
        ),
        migrations.CreateModel(
            name="Inflow",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("session_id", models.CharField(max_length=128, unique=True)),
                ("payment_reference", models.CharField(max_length=128, unique=True)),
                ("fingerprint", models.CharField(max_length=64)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("source_account", models.CharField(max_length=10)),
                ("source_bank", models.CharField(max_length=120)),
                ("occurred_at", models.DateTimeField()),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("held", models.BooleanField(default=False)),
                ("account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="inflows", to="vas_harness.virtualaccount")),
            ],
            options={
                "indexes": [models.Index(fields=["account", "occurred_at"], name="vas_sim_history_idx")],
                "constraints": [models.CheckConstraint(condition=models.Q(amount__gt=0), name="vas_sim_amount_positive")],
            },
        ),
    ]
