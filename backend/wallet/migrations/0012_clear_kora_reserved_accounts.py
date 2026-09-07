from django.db import migrations


def clear_kora_accounts(apps, schema_editor):
    """Kora -> Monnify cutover: wipe dead dedicated-account details.

    Reserved NUBANs minted under the old rail (Kora) are dead now that Monnify is
    the sole money-movement rail — Monnify never issued them, so money transferred
    to a stored Kora account is never credited. Both ensure_reserved_account and
    wallet_account_create short-circuit when account_number is already set, so an
    affected user would keep seeing (and be told to fund) the dead Kora number and
    would never be re-provisioned onto Monnify.

    Clear the stored funding-account fields so the app stops showing the dead
    number and re-mints (or recovers, via Monnify's stable accountReference) a
    Monnify account on the next BVN verification / account-create / balance read.
    Wallet BALANCES (real ledger money) are untouched.

    Note: this also clears already-valid Monnify NUBANs for any wallet that was
    already on Monnify — that is safe, because ensure_reserved_account recovers the
    same account from Monnify by our stable ``ZITCH-WALLET-{id}`` accountReference
    (no BVN needed for the get), so those users get their identical number back.
    Mirrors 0010 (the earlier Monnify -> Kora cutover), in reverse.
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
    # Irreversible: the dead Kora NUBANs are not worth (and cannot be safely)
    # restoring. Re-provisioning mints/recovers fresh Monnify accounts.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("wallet", "0011_alter_currencywallet_user_alter_fundingintent_user_and_more"),
    ]

    operations = [
        migrations.RunPython(clear_kora_accounts, noop_reverse),
    ]
