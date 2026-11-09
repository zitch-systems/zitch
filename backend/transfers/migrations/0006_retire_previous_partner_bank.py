"""Retire the previous account partner from new payout bank choices.

Financial audit and historical beneficiary rows are intentionally unchanged.
"""
from django.db import migrations


def retire_previous_partner(apps, schema_editor):
    Bank = apps.get_model("transfers", "Bank")
    Bank.objects.using(schema_editor.connection.alias).filter(code="9psb").update(active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("transfers", "0005_beneficiary_save_offer_sent"),
    ]

    operations = [
        migrations.RunPython(retire_previous_partner, reverse_code=migrations.RunPython.noop),
    ]
