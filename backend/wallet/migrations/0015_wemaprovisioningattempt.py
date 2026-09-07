from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("wallet", "0014_wallet_bank_tier"),
    ]

    operations = [
        migrations.CreateModel(
            name="WemaProvisioningAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("tracking_id", models.CharField(max_length=160)),
                ("identity_type", models.CharField(
                    choices=[("bvn", "BVN"), ("nin", "NIN")], max_length=3)),
                ("identity_hash", models.CharField(max_length=64)),
                ("identity_last4", models.CharField(max_length=4)),
                ("status", models.CharField(
                    choices=[("pending", "pending"), ("verified", "verified"),
                             ("failed", "failed")], default="pending", max_length=10)),
                ("expires_at", models.DateTimeField()),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="wema_provisioning_attempts", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="wemaprovisioningattempt",
            constraint=models.UniqueConstraint(
                fields=("user", "tracking_id"), name="uniq_user_wema_tracking_id"),
        ),
        migrations.AddIndex(
            model_name="wemaprovisioningattempt",
            index=models.Index(fields=["user", "status", "expires_at"],
                               name="wema_attempt_lookup_idx"),
        ),
    ]
