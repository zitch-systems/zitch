from django.db import migrations


def clear_monnify_accounts(apps, schema_editor):
    """Monnify -> Kora cutover: wipe dead dedicated-account details.

    Reserved NUBANs minted under the old rail (Monnify) are dead now that Kora is
    the sole money-movement rail — Kora never issued them, so money transferred to a
    stored Monnify account is never credited. Both ensure_reserved_account and
    wallet_account_create short-circuit when account_number is already set, so an
    affected user would keep seeing (and be told to fund) the dead Monnify number and
    would never be re-provisioned onto Kora.

    Clear the stored funding-account fields so the app stops showing the dead number
    and re-mints (or recovers, via Kora's stable ``ZITCH-WALLET-{id}`` account
    reference) a Kora account on the next BVN verification / account-create / balance
    read. Wallet BALANCES (real ledger money) are untouched.

    Note: this also clears already-valid Kora NUBANs for any wallet that was already
    on Kora — that is safe, because ensure_reserved_account recovers the same account
    from Kora by our stable ``ZITCH-WALLET-{id}`` account_reference (no BVN needed for
    the get), so those users get their identical number back. Mirrors 0012 (the
    earlier Kora -> Monnify cutover), in reverse.
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
    # restoring. Re-provisioning mints/recovers fresh Kora accounts.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0012_clear_kora_reserved_accounts"),
    ]

    operations = [
        migrations.RunPython(clear_monnify_accounts, noop_reverse),
    ]
