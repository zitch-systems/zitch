from django.db import migrations


def clear_legacy_accounts(apps, schema_editor):
    """Monnify -> Kora cutover: wipe dead dedicated-account details.

    Reserved NUBANs minted under the old rail (Monnify) are dead — Kora never
    issued them, so money transferred to one is never credited. The Monnify
    removal never cleared these stored fields, and both ensure_reserved_account
    and wallet_account_create short-circuit when account_number is set, so
    affected users keep seeing (and are told to fund) the dead number and are
    never re-provisioned onto Kora.

    Clear the stored funding-account fields so the app stops showing the dead
    number and re-mints a Kora account on the next BVN / account-create. Wallet
    BALANCES (real ledger money) are untouched. Safe to run now because the
    short-circuit means no Kora account can have been provisioned yet (the stale
    Monnify number blocks it), so this only clears dead Monnify records.
    """
    Wallet = apps.get_model("wallet", "Wallet")
    Wallet.objects.exclude(account_number="").update(
        account_number="",
        bank_name="",
        account_name="",
        account_reference="",
        bank_accounts=[],
    )


def noop_reverse(apps, schema_editor):
    # Irreversible: the dead Monnify NUBANs are not worth (and cannot be safely)
    # restoring. Re-provisioning mints fresh Kora accounts.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0009_fundingintent_meta"),
    ]

    operations = [
        migrations.RunPython(clear_legacy_accounts, noop_reverse),
    ]
