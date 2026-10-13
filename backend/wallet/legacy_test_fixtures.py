"""Synthetic historical records for regression tests; no retired bank operations."""
from decimal import Decimal
from uuid import uuid4

from django.utils import timezone

from legacy_funding.models import Receipt, VirtualAccount
from wallet.models import BillFundingBinding, Wallet
from wallet.services import credit, debit, get_or_create_wallet
from wallet.tests import make_user


def account_fixture(suffix="1", mode="live"):
    user, _ = make_user("0809990000" + suffix, "historical" + suffix + "@zitch.test", tier=3)
    prefix = "711" if mode == "validation" else "999"
    return user, VirtualAccount.objects.create(
        user=user, number=prefix + "000000" + suffix, display_name="Zitch/Ada " + suffix,
        encrypted_identity="retained-encrypted-fixture", verification_reference="historical-proof",
        consent_reference="historical-consent", verified_at=timezone.now(), mode=mode, prefix=prefix)


def retained_receipt(account, amount="1000", *, state=Receipt.CREDITED):
    """Restore a synthetic prior record, not a callback or enrollment."""
    identifier = uuid4().hex
    txn = (credit(account.user, Decimal(amount), "Historical funding", reference="RETAINED" + identifier)
           if state == Receipt.CREDITED else None)
    return Receipt.objects.create(
        account=account, session_id="session-" + identifier,
        payment_reference="payment-" + identifier, fingerprint=identifier,
        amount=amount, source_account="0000000000", source_bank="Historical bank",
        occurred_at=timezone.now(), state=state, transaction=txn)


def retained_bill(account, amount="100", *, source="0123456789"):
    """Synthetic reservation written before retirement, with immutable attribution."""
    txn = debit(account.user, amount, "Airtime — MTN", enforce_limits=False)
    BillFundingBinding.objects.create(transaction=txn, source_account=source,
        vas_account=account, approval_reference="historical-approval")
    return txn
