"""Delete every remaining 9PSB and Wema VAS table, for good.

Approved by the operator on 2026-11-12. Zitch runs on Wema Partnership only.

Customer money is NOT touched: wallet balances, the transaction ledger and
UnsettledFunding are Zitch's own records and stay. Before a retired table is
dropped, each row's non-secret fields are copied into the append-only AuditLog
(action ``retired_provider_row_archived``) so a customer's former account and
any archived balance can still be traced. Encrypted and BVN columns are never
copied; they are destroyed with the table.

Roll-forward only: the old schema is never restored.
"""
import datetime
import decimal

from django.db import migrations, transaction

RETIRED_PREFIXES = ("banking_", "wema_vas_")


def _plain(value):
    if isinstance(value, (decimal.Decimal, datetime.date, datetime.datetime)):
        return str(value)
    return value


def purge(apps, schema_editor):
    # One transaction: LOCK TABLE needs it, and a failure must leave every
    # table in place rather than half the set dropped.
    with transaction.atomic(using=schema_editor.connection.alias):
        _purge(apps, schema_editor)


def _purge(apps, schema_editor):
    connection = schema_editor.connection
    AuditLog = apps.get_model("whatsapp", "AuditLog")
    Bank = apps.get_model("transfers", "Bank")
    alias = connection.alias
    tables = sorted(t for t in connection.introspection.table_names()
                    if t.startswith(RETIRED_PREFIXES))
    archived = 0
    with connection.cursor() as cursor:
        if tables and connection.vendor == "postgresql":
            cursor.execute("LOCK TABLE " + ", ".join(map(schema_editor.quote_name, tables))
                           + " IN ACCESS EXCLUSIVE MODE")
        for table in tables:
            cursor.execute("SELECT * FROM " + schema_editor.quote_name(table))
            columns = [c[0] for c in cursor.description]
            for row in cursor.fetchall():
                record = {col: _plain(val) for col, val in zip(columns, row)
                          if "encrypted" not in col and "bvn" not in col}
                AuditLog.objects.using(alias).create(
                    actor_type="system", actor_id="wallet.0034",
                    action="retired_provider_row_archived",
                    target=f"{table}:{record.get('id', '')}"[:120], after=record)
                archived += 1
        # No CASCADE: anything outside the retired set that still depends on
        # these tables stops the migration instead of being silently dropped.
        if tables and connection.vendor == "postgresql":
            cursor.execute("DROP TABLE " + ", ".join(map(schema_editor.quote_name, tables)))
        else:
            for table in tables:
                cursor.execute("DROP TABLE " + schema_editor.quote_name(table))
        if connection.vendor == "postgresql":
            cursor.execute("DROP FUNCTION IF EXISTS wema_vas_account_immutable_fn()")
            cursor.execute("DROP FUNCTION IF EXISTS wema_vas_evidence_immutable_fn()")
    deleted, _ = Bank.objects.using(alias).filter(code="9psb").delete()
    print(f"Retired providers purged: {len(tables)} table(s) dropped, {archived} row(s) "
          f"copied to AuditLog, {deleted} 9PSB bank entry removed. Balances untouched.")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0033_detach_9psb_wallet_mappings"),
                    ("whatsapp", "0015_wamessagelog_wamsg_inbound_queue_idx_and_more"),
                    ("transfers", "0006_retire_previous_partner_bank")]
    operations = [migrations.RunPython(purge)]
