import uuid

from django.db import IntegrityError, migrations, models


def populate_spend_namespaces(apps, schema_editor):
    """Fill pre-existing users without holding the AddField lock for the scan."""
    User = apps.get_model("accounts", "User")
    user_ids = (User.objects.filter(spend_namespace__isnull=True)
                .values_list("pk", flat=True).iterator(chunk_size=500))
    for user_id in user_ids:
        # A current application request may have repaired this row after the
        # iterator read it. The conditional update preserves that winner.
        for _attempt in range(3):
            try:
                updated = User.objects.filter(
                    pk=user_id, spend_namespace__isnull=True,
                ).update(spend_namespace=uuid.uuid4())
            except IntegrityError:
                # A generated UUID colliding with another user is fantastically
                # unlikely, but retrying is cheap and keeps a partial backfill
                # safely resumable.
                continue
            if updated or User.objects.filter(
                    pk=user_id, spend_namespace__isnull=False).exists():
                break
        else:
            raise RuntimeError(
                f"Unable to allocate a unique spend namespace for user {user_id}"
            )


class Migration(migrations.Migration):
    # Each row commits independently. A large user table therefore does not keep
    # one long transaction (or the preceding AddField lock) open for the whole
    # backfill. If interrupted, rerunning only visits remaining NULL rows.
    atomic = False

    dependencies = [
        ("accounts", "0028_user_spend_namespace"),
    ]

    operations = [
        # Removing these persisted identities would orphan unresolved mobile
        # idempotency attempts and let a later re-upgrade allocate different
        # namespaces. Roll back application code with the additive column still
        # present; do not pretend this data migration is safely reversible.
        migrations.RunPython(populate_spend_namespaces),
        # Application default only. A normal AlterField would introduce a
        # temporary database default while rebuilding/altering the table; the
        # schema already has exactly the nullable unique column we want.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="user",
                    name="spend_namespace",
                    field=models.UUIDField(
                        default=uuid.uuid4, editable=False, null=True, unique=True,
                    ),
                ),
            ],
        ),
    ]
