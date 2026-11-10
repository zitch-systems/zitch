from django.db import migrations, models
import django.db.models.deletion


def install(_apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("""
            CREATE TRIGGER wallet_funding_resolution_immutable BEFORE UPDATE OR DELETE
            ON wallet_fundingholdresolution FOR EACH ROW
            EXECUTE FUNCTION wallet_unsettled_funding_immutable_fn();
        """)


def remove(_apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP TRIGGER IF EXISTS wallet_funding_resolution_immutable "
                              "ON wallet_fundingholdresolution")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0031_drop_retired_account_schema")]
    operations = [
        migrations.CreateModel(
            name="FundingHoldResolution",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(default="operator_write_off", max_length=32)),
                ("operator_reference", models.CharField(max_length=160)),
                ("created", models.DateTimeField(auto_now_add=True)),
                ("funding", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT,
                                                related_name="resolution", to="wallet.unsettledfunding")),
            ],
            options={"constraints": [models.CheckConstraint(condition=models.Q(kind="operator_write_off"),
                                                             name="funding_resolution_writeoff_only")]},
        ),
        migrations.RunPython(install, remove),
    ]
