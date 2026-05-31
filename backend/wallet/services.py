"""Wallet ledger operations — the heart of the money logic.

Every debit/credit goes through here so balance changes and ledger rows are
always written together, atomically, with row locking to prevent double-spend.
"""
import secrets
from decimal import Decimal

from django.db import transaction as db_transaction

from .models import FundingIntent, Transaction, Wallet


class InsufficientFunds(Exception):
    pass


def make_reference(prefix: str = "ZTCH") -> str:
    return f"{prefix}{secrets.token_hex(6).upper()}"


def get_or_create_wallet(user) -> Wallet:
    wallet, _ = Wallet.objects.get_or_create(user=user)
    return wallet


@db_transaction.atomic
def debit(user, amount, service: str, meta: dict | None = None, reference: str | None = None) -> Transaction:
    """Atomically debit the wallet and write a PENDING ledger row.

    Raises InsufficientFunds if the balance can't cover `amount`. The caller
    flips the row to Successful/Failed after the provider responds.
    """
    amount = Decimal(str(amount))
    wallet = Wallet.objects.select_for_update().get(user=user)
    if wallet.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance")
    wallet.balance -= amount
    wallet.save(update_fields=["balance", "updated"])
    return Transaction.objects.create(
        user=user,
        service=service,
        amount=amount,
        direction=Transaction.OUT,
        transaction_status=Transaction.PENDING,
        reference=reference or make_reference(),
        meta=meta or {},
    )


@db_transaction.atomic
def credit(user, amount, service: str, meta: dict | None = None, reference: str | None = None) -> Transaction:
    amount = Decimal(str(amount))
    wallet = Wallet.objects.select_for_update().get(user=user)
    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated"])
    return Transaction.objects.create(
        user=user,
        service=service,
        amount=amount,
        direction=Transaction.IN,
        transaction_status=Transaction.SUCCESS,
        reference=reference or make_reference("ZFND"),
        meta=meta or {},
    )


@db_transaction.atomic
def refund(txn: Transaction) -> None:
    """Reverse a failed debit and mark the row Failed."""
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status"])


@db_transaction.atomic
def settle_funding(reference: str, verified_amount=None) -> Transaction | None:
    """Credit the wallet for a verified funding reference, exactly once.

    Locks the FundingIntent row so concurrent calls (the app's verify request
    AND the Paystack webhook hitting at the same time) can't double-credit.
    Returns the credit Transaction if this call performed the credit, else None.
    """
    try:
        intent = FundingIntent.objects.select_for_update().get(reference=reference)
    except FundingIntent.DoesNotExist:
        return None

    if intent.credited:
        return None  # already funded — idempotent no-op

    amount = Decimal(str(verified_amount)) if verified_amount is not None else intent.amount
    txn = credit(intent.user, amount, "Wallet top-up", meta={"reference": reference}, reference=reference)

    intent.status = FundingIntent.PAID
    intent.credited = True
    intent.amount = amount
    intent.save(update_fields=["status", "credited", "amount", "updated"])
    return txn
