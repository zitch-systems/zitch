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
def settle_or_refund(txn: Transaction, result: dict) -> str:
    """Resolve a PENDING provider-backed debit from the provider's result.

    Returns one of:
      "success" — provider delivered; row marked Successful.
      "pending" — outcome unknown (e.g. a send timeout); row left Pending and
                  flagged ``meta.reconcile`` so the reconcile job requeries it.
                  The money stays debited — we never refund a maybe-delivered
                  purchase, which would leak money if it actually went through.
      "failed"  — definitive failure; wallet refunded, row marked Failed.

    Locks the row and guards on its status, so a later reconcile call can't
    double-settle (credit twice / mark a delivered purchase failed).
    """
    txn = Transaction.objects.select_for_update().get(pk=txn.pk)
    if txn.transaction_status == Transaction.SUCCESS:
        return "success"
    if txn.transaction_status == Transaction.FAILED:
        return "failed"

    meta = dict(txn.meta or {})
    if result.get("success"):
        meta.pop("reconcile", None)
        meta.update({k: v for k, v in result.items() if k != "raw"})
        txn.meta = meta
        txn.transaction_status = Transaction.SUCCESS
        txn.save(update_fields=["transaction_status", "meta"])
        return "success"
    if result.get("pending"):
        if not meta.get("reconcile"):
            meta["reconcile"] = True
            txn.meta = meta
            txn.save(update_fields=["meta"])
        return "pending"

    # Definitive failure: refund and mark Failed.
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    meta.pop("reconcile", None)
    meta["failure"] = result.get("message", "")
    txn.meta = meta
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status", "meta"])
    return "failed"


def run_provider_purchase(user, amount, service: str, meta: dict, provider_call):
    """Debit the wallet (PENDING) → call the provider → settle the row.

    ``provider_call(reference)`` receives the ledger reference to use as the
    provider's idempotency key and returns the provider result dict. The network
    call runs OUTSIDE the debit transaction, so no row lock is held during I/O.
    Returns ``(status, txn, result)`` where status is the settle_or_refund code.
    Raises InsufficientFunds (the caller maps it to a 402).
    """
    txn = debit(user, amount, service, meta=meta)
    result = provider_call(txn.reference)
    status = settle_or_refund(txn, result)
    return status, txn, result


@db_transaction.atomic
def reverse_transfer(reference: str) -> Transaction | None:
    """Refund a settled outbound transfer the provider later failed/reversed.

    Bank payouts are settled optimistically on send, so the disbursement webhook
    is the safety net. Locks the row and guards on status, so only the first
    call (while the row is still Successful/Pending) credits the money back and
    marks it Failed — duplicate webhooks can't double-refund. Returns the row if
    this call performed the reversal, else None.
    """
    txn = (
        Transaction.objects.select_for_update()
        .filter(reference=reference, direction=Transaction.OUT)
        .first()
    )
    if txn is None or txn.transaction_status == Transaction.FAILED:
        return None
    wallet = Wallet.objects.select_for_update().get(user=txn.user)
    wallet.balance += txn.amount
    wallet.save(update_fields=["balance", "updated"])
    txn.transaction_status = Transaction.FAILED
    txn.save(update_fields=["transaction_status"])
    return txn


@db_transaction.atomic
def settle_funding(reference: str, verified_amount=None) -> Transaction | None:
    """Credit the wallet for a verified funding reference, exactly once.

    Locks the FundingIntent row so concurrent calls (the app's verify request
    AND the Monnify webhook hitting at the same time) can't double-credit.
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


@db_transaction.atomic
def transfer(sender, recipient, amount, note: str = "") -> tuple[Transaction, Transaction]:
    """Move funds between two Zitch wallets atomically.

    Both wallet rows are locked (in a stable order to avoid deadlocks) so the
    debit and credit either both happen or neither does. Raises InsufficientFunds
    if the sender can't cover the amount. Returns (debit_txn, credit_txn).
    """
    amount = Decimal(str(amount))

    # Lock both wallets in a deterministic order (by user id) to prevent
    # deadlocks when two users transfer to each other simultaneously.
    first, second = sorted([sender.id, recipient.id])
    wallets = {w.user_id: w for w in Wallet.objects.select_for_update().filter(user_id__in=[first, second])}
    sw = wallets[sender.id]
    rw = wallets[recipient.id]

    if sw.balance < amount:
        raise InsufficientFunds("Insufficient wallet balance")

    ref = make_reference("ZTRF")
    sw.balance -= amount
    rw.balance += amount
    sw.save(update_fields=["balance", "updated"])
    rw.save(update_fields=["balance", "updated"])

    recipient_name = (recipient.get_full_name() or recipient.phone or "Zitch user").strip()
    sender_name = (sender.get_full_name() or sender.phone or "Zitch user").strip()

    debit_txn = Transaction.objects.create(
        user=sender, service=f"Transfer to {recipient_name}", amount=amount,
        direction=Transaction.OUT, transaction_status=Transaction.SUCCESS,
        reference=ref, meta={"to": recipient.phone, "note": note},
    )
    credit_txn = Transaction.objects.create(
        user=recipient, service=f"Transfer from {sender_name}", amount=amount,
        direction=Transaction.IN, transaction_status=Transaction.SUCCESS,
        reference=f"{ref}-C", meta={"from": sender.phone, "note": note},
    )
    return debit_txn, credit_txn
