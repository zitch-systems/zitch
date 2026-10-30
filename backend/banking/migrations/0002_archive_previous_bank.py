"""Preserve the previous bank's book liability without making it 9PSB money."""
from django.db import migrations


def archive(apps, schema_editor):
    Wallet = apps.get_model("wallet", "Wallet")
    Transaction = apps.get_model("wallet", "Transaction")
    Archive = apps.get_model("banking", "PreviousBankArchive")
    User = apps.get_model("accounts", "User")
    if schema_editor.connection.vendor == "postgresql":
        # Existing credentials and new-business modes are retired before release.
        # Lock snapshots against any final in-flight ledger write.
        schema_editor.execute("LOCK TABLE wallet_wallet IN SHARE ROW EXCLUSIVE MODE")
    count = 0
    profiles = {u.pk: {"tier": u.tier, "bvn_verified": u.bvn_verified, "nin_verified": u.nin_verified,
                       "face_verified": u.face_verified, "address_verified": u.address_verified}
                for u in User.objects.all().iterator()}
    for wallet in Wallet.objects.all().iterator():
        if Archive.objects.filter(wallet_id=wallet.pk).exists():
            continue
        pending = Transaction.objects.filter(user_id=wallet.user_id,
            direction="out", transaction_status="Pending").count()
        Archive.objects.create(wallet_id=wallet.pk, account_number=wallet.account_number,
            account_name=wallet.account_name, bank_name=wallet.bank_name,
            account_reference=wallet.account_reference, balance=wallet.balance,
            account_metadata={"bank_accounts": wallet.bank_accounts, "bank_tier": wallet.bank_tier,
                              "pnd_lifted": wallet.pnd_lifted, "identity_state": profiles.get(wallet.user_id, {})},
            pending_transactions=pending, review_required=bool(wallet.balance or pending or wallet.account_number))
        # Only the immutable archive retains the retired funding destination.
        # Existing app/WhatsApp builds must not suggest transferring into it.
        Wallet.objects.filter(pk=wallet.pk).update(account_number="", account_name="",
            bank_name="", account_reference="", bank_accounts=[], bank_tier=0, pnd_lifted=False)
        count += 1
    # Historical identity evidence remains in its original tables. A previous
    # bank tier or facial approval is not an approval from the new bank.
    User.objects.update(tier=0, face_verified=False, address_verified=False)
    print(f"Previous bank archived: {count} wallet snapshots; ledger and total liabilities unchanged.")


def protect(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("""
        CREATE FUNCTION protect_previous_bank_archive() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Previous bank evidence is immutable'; END;
        $$;
        CREATE TRIGGER protect_previous_bank_archive
        BEFORE UPDATE OR DELETE ON banking_previousbankarchive
        FOR EACH ROW EXECUTE FUNCTION protect_previous_bank_archive();
    """)


class Migration(migrations.Migration):
    dependencies = [("banking", "0001_initial")]
    operations = [migrations.RunPython(archive), migrations.RunPython(protect)]
