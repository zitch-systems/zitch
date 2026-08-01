from django.db import migrations, models
from django.db.models import Count, Q


def assert_identity_hashes_are_unique(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    for field in ("bvn_hash", "nin_hash"):
        duplicates = list(
            User.objects.exclude(**{field: ""})
            .values(field).annotate(total=Count("id"))
            .filter(total__gt=1).values_list(field, flat=True)[:5]
        )
        if duplicates:
            raise RuntimeError(
                f"Cannot enforce unique {field}: duplicate identity ownership exists. "
                "Resolve the affected customers through compliance review before retrying."
            )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0014_operatortotp")]

    operations = [
        migrations.RunPython(assert_identity_hashes_are_unique, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("bvn_hash",), condition=~Q(bvn_hash=""),
                name="uniq_user_bvn_hash"),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                fields=("nin_hash",), condition=~Q(nin_hash=""),
                name="uniq_user_nin_hash"),
        ),
    ]
