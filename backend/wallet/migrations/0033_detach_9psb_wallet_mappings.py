"""Detach exact 9PSB wallet mappings while retaining provider and ledger evidence."""
from django.db import migrations


def detach_retired_mappings(apps, schema_editor):
    connection = schema_editor.connection
    if "banking_bankaccount" not in connection.introspection.table_names():
        return

    Wallet = apps.get_model("wallet", "Wallet")
    Transaction = apps.get_model("wallet", "Transaction")
    AuditLog = apps.get_model("whatsapp", "AuditLog")
    alias = connection.alias
    with connection.cursor() as cursor:
        sql = (
            "SELECT a.id, w.id, a.number FROM banking_bankaccount a "
            "JOIN wallet_wallet w ON w.id = a.wallet_id AND w.account_number = a.number "
            "WHERE w.account_number <> '' ORDER BY w.id"
        )
        if connection.vendor == "postgresql":
            sql += " FOR UPDATE OF a, w"
        cursor.execute(sql)
        matches = cursor.fetchall()

    cleared = {"account_number": "", "account_name": "", "bank_name": "",
               "account_reference": "", "bank_tier": 0, "pnd_lifted": False,
               "identity_upgrade_required": False}
    detached = skipped = 0
    for record_id, wallet_id, number in matches:
        wallet = (Wallet.objects.using(alias).select_for_update()
                  .filter(pk=wallet_id, account_number=number).first())
        if (wallet is None or wallet.bank_accounts
                or Transaction.objects.using(alias).filter(
                    user_id=wallet.user_id, transaction_status="Pending").exists()):
            skipped += 1
            continue
        before = {name: getattr(wallet, name) for name in cleared}
        AuditLog.objects.using(alias).create(
            actor_type="system", actor_id="wallet.0033", action="retired_9psb_mapping_detached",
            target=f"wallet:{wallet_id}", before=before,
            after={"retired_provider_record_id": record_id, "account_number": ""},
        )
        Wallet.objects.using(alias).filter(pk=wallet_id).update(**cleared)
        detached += 1
    print(f"9PSB wallet mappings detached: {detached}; separate review needed: {skipped}")


class Migration(migrations.Migration):
    dependencies = [("wallet", "0032_fundingholdresolution"),
                    ("whatsapp", "0015_wamessagelog_wamsg_inbound_queue_idx_and_more")]
    operations = [migrations.RunPython(detach_retired_mappings, migrations.RunPython.noop)]
