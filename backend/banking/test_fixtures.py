"""Synthetic bank eligibility for tests of shared application controls.

Fixtures create evidence directly; they never call a bank or open a customer
activation switch in production. Each caller explicitly overrides NINEPSB.
"""
from decimal import Decimal

from django.utils import timezone

from accounts.models import hash_identifier, record_identity_proof
from wallet.models import Wallet
from .models import AccountUpgrade, AddressVerification, BankAccount


def bind_test_bank(user, *, tier=None, available_balance=None):
    tier = user.tier if tier is None else tier
    wallet, _ = Wallet.objects.get_or_create(user=user)
    number = f"19{user.pk:08d}"
    name = user.get_full_name() or "Test Customer"
    wallet.account_number, wallet.account_name = number, name
    wallet.bank_name, wallet.bank_tier = "9PSB", tier
    wallet.save(update_fields=["account_number", "account_name", "bank_name", "bank_tier"])
    amount = wallet.balance if available_balance is None else Decimal(str(available_balance))
    bank, _ = BankAccount.objects.update_or_create(wallet=wallet, defaults={
        "number": number, "name": name, "environment": "production", "tier": tier,
        "pnd": False, "frozen": False, "status": "active", "checked_at": timezone.now(),
        "available_balance": amount, "ledger_balance": amount, "opening_review_required": False})
    if tier >= 1:
        record_identity_proof(user, "bvn", f"{10000000000 + user.pk}", source="9psb_otp")
    if tier >= 2:
        record_identity_proof(user, "nin", f"{20000000000 + user.pk}", source="9psb_otp")
        address = None
        if tier == 3:
            address = AddressVerification.objects.create(user=user,
                reference=f"TEST-ADDRESS-{user.pk}", address_digest="test-address",
                encrypted_address="test-fixture", state="verified")
            user.address_verified = True
        AccountUpgrade.objects.create(account=bank, tier=tier, reference=f"TEST-UPGRADE-{user.pk}",
            bvn_hash=hash_identifier(f"{10000000000 + user.pk}"),
            nin_hash=hash_identifier(f"{20000000000 + user.pk}"), request_digest="test-upgrade",
            state="approved", address_verification=address)
    user.recompute_tier()
    user.save(update_fields=["tier", "address_verified"])
    return bank


def make_bank_user(*args, bank_balance=None, **kwargs):
    from wallet.tests import make_user
    user, token = make_user(*args, **kwargs)
    bind_test_bank(user, available_balance=bank_balance)
    return user, token
