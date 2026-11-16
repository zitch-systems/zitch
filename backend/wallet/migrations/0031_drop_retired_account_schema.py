"""Drop the empty retired schema after all services use the detached runtime."""
from django.db import migrations


RETIRED_TABLES = ("wema_vas_receipt", "wema_vas_migrationapproval", "wema_vas_virtualaccount")


def drop_empty_schema(_apps, schema_editor):
    """Drop whatever is left of the retired wema_vas schema, by name.

    The tombstone app that described these tables has been deleted, so this works
    from table names alone and is a no-op where they never existed (every fresh
    database). A retired table that still holds rows stops the migration.
    """
    connection = schema_editor.connection
    existing = set(connection.introspection.table_names())
    present = [table for table in RETIRED_TABLES if table in existing]
    with connection.cursor() as cursor:
        binding_columns = {c.name for c in connection.introspection.get_table_description(
            cursor, "wallet_billfundingbinding")}
    if connection.vendor == "postgresql" and present:
        # Close the count/drop race and wait for any final old-schema statement.
        tables = ["wallet_billfundingbinding"] + present
        schema_editor.execute("LOCK TABLE " + ", ".join(map(schema_editor.quote_name, tables))
                              + " IN ACCESS EXCLUSIVE MODE")
    with connection.cursor() as cursor:
        counts = {}
        for table in present:
            cursor.execute("SELECT COUNT(*) FROM " + schema_editor.quote_name(table))
            counts[table] = cursor.fetchone()[0]
        if "vas_account_id" in binding_columns:
            cursor.execute("SELECT COUNT(*) FROM wallet_billfundingbinding WHERE vas_account_id IS NOT NULL")
            counts["linked_bills"] = cursor.fetchone()[0]
    if any(counts.values()):
        raise RuntimeError(f"Retired account schema is not empty; refusing removal: {counts}")
    if connection.vendor == "postgresql":
        # Keep the active bill reservation/refund checks; only remove the retired
        # account branch. Immutability triggers remain enabled throughout.
        schema_editor.execute("""
            CREATE OR REPLACE FUNCTION wallet_bill_funding_insert_fn()
            RETURNS trigger AS $$ BEGIN
                IF TG_TABLE_NAME = 'wallet_billfundingbinding' THEN
                    IF NOT EXISTS (SELECT 1 FROM wallet_transaction t
                        WHERE t.id = NEW.transaction_id AND t.direction = 'out'
                          AND t.currency = 'NGN' AND t.transaction_status = 'Pending') THEN
                        RAISE EXCEPTION 'Invalid bill funding reservation'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                ELSE
                    IF NOT EXISTS (SELECT 1 FROM wallet_billfundingbinding b
                        JOIN wallet_transaction t ON t.id = b.transaction_id
                        WHERE b.id = NEW.binding_id AND t.transaction_status = 'Failed') THEN
                        RAISE EXCEPTION 'Bill funding release requires a failed debit'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END; $$ LANGUAGE plpgsql
        """)
    if "vas_account_id" in binding_columns:
        schema_editor.execute("ALTER TABLE wallet_billfundingbinding DROP COLUMN vas_account_id")
    for table in present:
        # Deliberately no CASCADE: unexpected dependencies must stop removal.
        schema_editor.execute("DROP TABLE " + schema_editor.quote_name(table))
    if connection.vendor == "postgresql":
        schema_editor.execute("DROP FUNCTION IF EXISTS wema_vas_account_immutable_fn()")
        schema_editor.execute("DROP FUNCTION IF EXISTS wema_vas_evidence_immutable_fn()")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0030_detach_retired_funding")]
    # Schema retirement is intentionally roll-forward only. Old provider code is
    # never restored by an application rollback.
    operations = [migrations.RunPython(drop_empty_schema)]
