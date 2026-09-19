import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count


def refuse_duplicate_cards(apps, schema_editor):
    """Do not guess which already-issued provider card is authoritative."""
    VirtualCard = apps.get_model("cards", "VirtualCard")
    duplicates = (VirtualCard.objects.values("user_id")
                  .annotate(total=Count("id"))
                  .filter(total__gt=1)
                  .count())
    if duplicates:
        raise RuntimeError(
            "Cannot enforce one virtual card per user: "
            f"{duplicates} user(s) have duplicate card rows. Resolve them against "
            "issuer evidence before applying this migration."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("cards", "0003_alter_virtualcard_user"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CardIssuance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("idempotency_key_hash", models.CharField(max_length=64)),
                ("reference", models.CharField(max_length=40, unique=True)),
                ("provider", models.CharField(max_length=20)),
                ("state", models.CharField(
                    choices=[
                        ("starting", "Starting"),
                        ("pending", "Pending review"),
                        ("succeeded", "Succeeded"),
                        ("failed", "Failed"),
                    ],
                    default="starting",
                    max_length=12,
                )),
                ("provider_reference", models.CharField(blank=True, default="", max_length=100)),
                ("provider_status", models.CharField(blank=True, default="", max_length=40)),
                ("message", models.CharField(blank=True, default="", max_length=300)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("updated", models.DateTimeField(auto_now=True)),
                ("card", models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="issuance",
                    to="cards.virtualcard",
                )),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="card_issuances",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created"],
                "indexes": [
                    models.Index(fields=["user", "state", "created"],
                                 name="cards_issue_user_state_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "idempotency_key_hash"),
                        name="cards_unique_issue_key_per_user",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("state__in", ("starting", "pending"))),
                        fields=("user",),
                        name="cards_one_active_issuance_per_user",
                    ),
                ],
            },
        ),
        migrations.RunPython(refuse_duplicate_cards, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="virtualcard",
            constraint=models.UniqueConstraint(
                fields=("user",),
                name="cards_one_card_per_user",
            ),
        ),
    ]
