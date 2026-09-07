"""Every PIN that exists today was set under the 4-digit rule, so every one of
them has to be replaced.

A password hash cannot be measured — there is no way to ask an existing row how
long its PIN was — so the only honest answer is to flag every account that
holds one and let each clear its own flag by setting a new 6-digit PIN.

Deliberately not reversible in the data sense: rolling back would clear flags
for accounts that may since have set a 6-digit PIN, re-demanding a reset they
already did. The column drop in 0018 is the rollback.
"""
from django.db import migrations


def flag_existing_pins(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.exclude(transaction_pin="").update(pin_reset_required=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0018_user_pin_reset_required")]
    operations = [migrations.RunPython(flag_existing_pins, migrations.RunPython.noop)]
