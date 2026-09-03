from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wallet", "0017_ledger_database_immutability")]

    operations = [
        migrations.AddField(
            model_name="wallet",
            name="pnd_lifted",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
