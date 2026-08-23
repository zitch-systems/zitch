"""Enforce ledger immutability below the Django ORM.

``Transaction.save()`` already rejects changes to the money-defining fields, but
``QuerySet.update()``, raw SQL and a compromised operator connection bypass model
methods completely.  On PostgreSQL, keep those paths from rewriting or deleting
the historical ledger as well.  SQLite remains unchanged so local development and
the test runner stay portable; production uses PostgreSQL.
"""

from django.db import migrations


FUNCTION_NAME = "wallet_transaction_immutable_guard_fn"
TRIGGER_NAME = "wallet_transaction_immutable_guard"


def install_guard(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    table = schema_editor.connection.ops.quote_name("wallet_transaction")
    schema_editor.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION_NAME}()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Ledger rows cannot be deleted'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF NEW.user_id IS DISTINCT FROM OLD.user_id
               OR NEW.service IS DISTINCT FROM OLD.service
               OR NEW.amount IS DISTINCT FROM OLD.amount
               OR NEW.direction IS DISTINCT FROM OLD.direction
               OR NEW.currency IS DISTINCT FROM OLD.currency
               OR NEW.reference IS DISTINCT FROM OLD.reference
               OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
               OR NEW.created IS DISTINCT FROM OLD.created THEN
                RAISE EXCEPTION
                    'Ledger identity and money fields are immutable'
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    schema_editor.execute(
        f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON {table}"
    )
    schema_editor.execute(
        f"""
        CREATE TRIGGER {TRIGGER_NAME}
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION {FUNCTION_NAME}()
        """
    )


def remove_guard(_apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    table = schema_editor.connection.ops.quote_name("wallet_transaction")
    schema_editor.execute(
        f"DROP TRIGGER IF EXISTS {TRIGGER_NAME} ON {table}"
    )
    schema_editor.execute(f"DROP FUNCTION IF EXISTS {FUNCTION_NAME}()")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0016_wemafacesession")]

    operations = [migrations.RunPython(install_guard, remove_guard)]
