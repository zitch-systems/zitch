"""Detach retired account models; leave empty tables for the rolling deployment."""
from django.db import migrations


def require_empty(apps, schema_editor):
    alias = schema_editor.connection.alias
    models = [apps.get_model("wema_vas", name)
              for name in ("VirtualAccount", "Receipt", "MigrationApproval")]
    counts = {model._meta.model_name: model.objects.using(alias).count() for model in models}
    counts["linked_bills"] = apps.get_model("wallet", "BillFundingBinding").objects.using(alias).filter(
        vas_account__isnull=False).count()
    if any(counts.values()):
        raise RuntimeError(f"Retired account schema is not empty; refusing removal: {counts}")
    print(f"Retired account schema preflight: {counts}; financial ledger and holds unchanged.")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0029_retired_funding_archive"),
                    ("wema_vas", "0004_separate_account_modes")]
    operations = [
        migrations.RunPython(require_empty, migrations.RunPython.noop),
        migrations.SeparateDatabaseAndState(state_operations=[
            migrations.RemoveField(model_name="billfundingbinding", name="vas_account"),
        ]),
    ]
