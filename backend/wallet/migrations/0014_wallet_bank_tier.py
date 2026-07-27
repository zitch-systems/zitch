from django.db import migrations, models


class Migration(migrations.Migration):
    """The partner bank's own tier for the NUBAN.

    Distinct from the user's Zitch KYC tier: the bank enforces its own per-tier
    inflow / daily-spend / balance caps on the account regardless of ours, so a
    transfer our ladder permits can still be refused at the gateway. 0 means "not
    read back yet" — no bank cap is applied until we have actually synced it.
    """

    dependencies = [
        ("wallet", "0013_clear_monnify_reserved_accounts"),
    ]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="bank_tier",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
