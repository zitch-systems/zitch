from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_wema_wallet_otp_proofs(apps, schema_editor):
    IdentityProof = apps.get_model("accounts", "IdentityProof")
    Attempt = apps.get_model("wallet", "WemaProvisioningAttempt")
    for attempt in Attempt.objects.filter(status="VERIFIED").exclude(tracking_id__startswith="KYC-STATUS-").iterator():
        source = "wema_wallet_otp"
        IdentityProof.objects.update_or_create(
            user_id=attempt.user_id,
            identity_type=attempt.identity_type,
            identity_hash=attempt.identity_hash,
            source=source,
            defaults={
                "identity_last4": attempt.identity_last4,
                "provider_reference": attempt.tracking_id[:128],
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0017_ledger_database_immutability"),
        ("accounts", "0024_refreshtoken"),
    ]

    operations = [
        migrations.CreateModel(
            name="IdentityProof",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identity_type", models.CharField(choices=[("bvn", "BVN"), ("nin", "NIN")], max_length=8)),
                ("identity_hash", models.CharField(max_length=64)),
                ("identity_last4", models.CharField(blank=True, default="", max_length=4)),
                ("source", models.CharField(choices=[("wema_wallet_otp", "Wema wallet OTP"), ("wema_tier2", "Wema Tier 2 upgrade"), ("identity_provider_otp", "Identity provider OTP")], max_length=32)),
                ("provider_reference", models.CharField(blank=True, default="", max_length=128)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="identity_proofs", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="identityproof",
            constraint=models.UniqueConstraint(fields=("user", "identity_type", "identity_hash", "source"), name="uniq_identity_proof_source"),
        ),
        migrations.RunPython(backfill_wema_wallet_otp_proofs, migrations.RunPython.noop),
    ]
