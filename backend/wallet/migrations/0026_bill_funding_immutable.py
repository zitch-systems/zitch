"""Keep bill funding attribution and once-only releases below mutable metadata."""
from django.db import migrations


def install(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION wallet_bill_funding_immutable_fn()
        RETURNS trigger AS $$ BEGIN
            RAISE EXCEPTION 'Bill funding evidence cannot be updated or deleted'
                USING ERRCODE = 'integrity_constraint_violation';
        END; $$ LANGUAGE plpgsql
    """)
    for table in ("wallet_billfundingbinding", "wallet_billfundingrefund"):
        schema_editor.execute(f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
                              "FOR EACH ROW EXECUTE FUNCTION wallet_bill_funding_immutable_fn()")
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION wallet_bill_funding_insert_fn()
        RETURNS trigger AS $$ BEGIN
            IF TG_TABLE_NAME = 'wallet_billfundingbinding' THEN
                IF NOT EXISTS (SELECT 1 FROM wallet_transaction t
                    WHERE t.id = NEW.transaction_id AND t.direction = 'out'
                      AND t.currency = 'NGN' AND t.transaction_status = 'Pending'
                      AND (NEW.vas_account_id IS NULL OR EXISTS (
                        SELECT 1 FROM wema_vas_virtualaccount v
                        WHERE v.id = NEW.vas_account_id AND v.user_id = t.user_id
                          AND v.mode = 'live' AND v.prefix <> '711'
                          AND v.number <> NEW.source_account
                          AND NEW.approval_reference <> ''))) THEN
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
    for table in ("wallet_billfundingbinding", "wallet_billfundingrefund"):
        schema_editor.execute(f"CREATE TRIGGER {table}_valid_insert BEFORE INSERT ON {table} "
                              "FOR EACH ROW EXECUTE FUNCTION wallet_bill_funding_insert_fn()")


def remove(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    for table in ("wallet_billfundingbinding", "wallet_billfundingrefund"):
        for suffix in ("immutable", "valid_insert"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {table}_{suffix} ON {table}")
    schema_editor.execute("DROP FUNCTION IF EXISTS wallet_bill_funding_immutable_fn()")
    schema_editor.execute("DROP FUNCTION IF EXISTS wallet_bill_funding_insert_fn()")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0025_billfundingbinding_billfundingrefund_and_more")]
    operations = [migrations.RunPython(install, remove)]
