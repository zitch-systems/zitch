"""Retain bank evidence below the ORM; production PostgreSQL is mandatory."""
from django.db import migrations


def install(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION wema_vas_evidence_immutable_fn()
        RETURNS trigger AS $$ BEGIN
            RAISE EXCEPTION 'VAS bank evidence cannot be updated or deleted'
                USING ERRCODE = 'integrity_constraint_violation';
        END; $$ LANGUAGE plpgsql
    """)
    for table in ("wema_vas_receipt", "wema_vas_migrationapproval"):
        quoted = schema_editor.quote_name(table)
        schema_editor.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {quoted} "
                              "FOR EACH ROW EXECUTE FUNCTION wema_vas_evidence_immutable_fn()")
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION wema_vas_account_immutable_fn()
        RETURNS trigger AS $$ BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'VAS accounts cannot be deleted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            IF NEW.user_id IS DISTINCT FROM OLD.user_id
               OR NEW.number IS DISTINCT FROM OLD.number
               OR NEW.mode IS DISTINCT FROM OLD.mode
               OR NEW.prefix IS DISTINCT FROM OLD.prefix
               OR NEW.display_name IS DISTINCT FROM OLD.display_name
               OR NEW.cutover_reference IS DISTINCT FROM OLD.cutover_reference
               OR NEW.created IS DISTINCT FROM OLD.created THEN
                RAISE EXCEPTION 'VAS account identity is immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    schema_editor.execute("""
        CREATE TRIGGER wema_vas_account_immutable BEFORE UPDATE OR DELETE ON wema_vas_virtualaccount
        FOR EACH ROW EXECUTE FUNCTION wema_vas_account_immutable_fn()
    """)


def remove(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in ("wema_vas_receipt", "wema_vas_migrationapproval"):
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_immutable ON {schema_editor.quote_name(table)}")
    schema_editor.execute("DROP TRIGGER IF EXISTS wema_vas_account_immutable ON wema_vas_virtualaccount")
    schema_editor.execute("DROP FUNCTION IF EXISTS wema_vas_evidence_immutable_fn()")
    schema_editor.execute("DROP FUNCTION IF EXISTS wema_vas_account_immutable_fn()")


class Migration(migrations.Migration):
    dependencies = [("wema_vas", "0002_virtualaccount_cutover_reference_migrationapproval")]
    operations = [migrations.RunPython(install, remove)]
