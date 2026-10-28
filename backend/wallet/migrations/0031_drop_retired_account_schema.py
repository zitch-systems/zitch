"""Drop the empty retired schema after all services use the detached runtime."""
import importlib

from django.db import migrations
from django.db.migrations.loader import MigrationLoader


def historical_apps(connection):
    return MigrationLoader(connection).project_state([
        ("wallet", "0029_retired_funding_archive"),
        ("wema_vas", "0004_separate_account_modes"),
    ]).apps


def drop_empty_schema(_apps, schema_editor):
    connection = schema_editor.connection
    old = historical_apps(connection)
    models = [old.get_model("wema_vas", name)
              for name in ("Receipt", "MigrationApproval", "VirtualAccount")]
    binding = old.get_model("wallet", "BillFundingBinding")
    if connection.vendor == "postgresql":
        # Close the count/drop race and wait for any final old-schema statement.
        tables = [binding._meta.db_table] + [model._meta.db_table for model in models]
        schema_editor.execute("LOCK TABLE " + ", ".join(map(schema_editor.quote_name, tables))
                              + " IN ACCESS EXCLUSIVE MODE")
    gate = importlib.import_module("wallet.migrations.0030_detach_retired_funding")
    gate.require_empty(old, schema_editor)
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
    schema_editor.remove_field(binding, binding._meta.get_field("vas_account"))
    for model in models:
        # Deliberately no CASCADE: unexpected dependencies must stop removal.
        schema_editor.execute("DROP TABLE " + schema_editor.quote_name(model._meta.db_table))
    if connection.vendor == "postgresql":
        schema_editor.execute("DROP FUNCTION wema_vas_account_immutable_fn()")
        schema_editor.execute("DROP FUNCTION wema_vas_evidence_immutable_fn()")
    print("Retired schema removed: 3 empty tables, bill foreign key and retired triggers; "
          "Partnership ledger, bill protections and outstanding liabilities retained.")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0030_detach_retired_funding"),
                    ("wema_vas", "0005_remove_runtime_models")]
    # Schema retirement is intentionally roll-forward only. Old provider code is
    # never restored by an application rollback.
    operations = [migrations.RunPython(drop_empty_schema)]
