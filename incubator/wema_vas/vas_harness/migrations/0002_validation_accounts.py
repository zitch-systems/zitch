from django.db import migrations, models


def protect_receipts(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE FUNCTION vas_reject_receipt_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'VAS receipts are append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER vas_receipt_immutable
          BEFORE UPDATE OR DELETE ON vas_harness_inflow
          FOR EACH ROW EXECUTE FUNCTION vas_reject_receipt_mutation();
    """)


def unprotect_receipts(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        DROP TRIGGER IF EXISTS vas_receipt_immutable ON vas_harness_inflow;
        DROP FUNCTION IF EXISTS vas_reject_receipt_mutation();
    """)


class Migration(migrations.Migration):
    dependencies = [("vas_harness", "0001_initial")]

    operations = [
        migrations.AddField(model_name="virtualaccount", name="consent_reference",
                            field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="virtualaccount", name="customer_reference",
                            field=models.CharField(blank=True, max_length=128, null=True, unique=True)),
        migrations.AddField(model_name="virtualaccount", name="display_name",
                            field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="virtualaccount", name="encrypted_identity",
                            field=models.TextField(blank=True)),
        migrations.AddField(model_name="virtualaccount", name="mode",
                            field=models.CharField(default="synthetic", max_length=10)),
        migrations.AddField(model_name="virtualaccount", name="verification_reference",
                            field=models.CharField(blank=True, max_length=160)),
        migrations.AddField(model_name="virtualaccount", name="verified_at",
                            field=models.DateTimeField(blank=True, null=True)),
        migrations.RunPython(protect_receipts, unprotect_receipts),
    ]
