"""Atomic state transitions for provider-backed virtual-card funding."""
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Q

from wallet.models import Transaction, Wallet
from wallet.services import debit

from .models import VirtualCard


CARD_FUNDING_DISPOSITIONS = {
    "confirm_loaded",
    "confirm_failed",
    "confirm_projection_applied",
    "apply_missing_projection",
}


class CardFundingPending(Exception):
    def __init__(self, reference: str):
        self.reference = reference
        super().__init__(reference)


@db_transaction.atomic
def claim_card_funding(user, card: VirtualCard, amount: Decimal, key) -> Transaction:
    """Serialize different-key loads per card and create one pending debit."""
    locked_card = (VirtualCard.objects.select_for_update()
                   .get(pk=card.pk, user=user))
    unresolved = (Transaction.objects
                  .filter(user=user, direction=Transaction.OUT,
                          meta__card_funding=True)
                  .filter(Q(transaction_status=Transaction.PENDING)
                          | Q(meta__card_balance_review=True))
                  .first())
    if unresolved is not None:
        raise CardFundingPending(unresolved.reference)
    return debit(
        user, amount, "Card funding",
        meta={"card": locked_card.id, "card_funding": True, "reconcile": True,
              "card_balance_applied": False},
        idempotency_key=key,
    )


def _card_funding_row(reference: str) -> Transaction:
    txn = (Transaction.objects.select_for_update().select_related("user")
           .filter(reference=str(reference or "").strip(),
                   direction=Transaction.OUT)
           .first())
    if txn is None or not (txn.meta or {}).get("card_funding"):
        raise ValueError("Card-funding transaction not found")
    return txn


def _locked_card(txn: Transaction) -> VirtualCard:
    try:
        card_id = int((txn.meta or {}).get("card") or 0)
    except (TypeError, ValueError):
        card_id = 0
    card = (VirtualCard.objects.select_for_update()
            .filter(pk=card_id, user=txn.user).first())
    if card is None:
        raise ValueError("The card bound to this funding transaction was not found")
    return card


def _provider_meta(result: dict) -> dict:
    """Persist small provider outcome fields without copying arbitrary raw data."""
    allowed = ("status", "message", "provider_reference", "platform_reference", "mock")
    return {key: result[key] for key in allowed if key in result}


@db_transaction.atomic
def finalize_card_funding(txn: Transaction, result: dict) -> str:
    """Apply a card-load outcome exactly once across ledger and card projection.

    New card-funding rows explicitly start with ``card_balance_applied=False``.
    That marker distinguishes a safe-to-apply new transition from legacy
    Successful rows whose local balance may already have been incremented by old
    code.  Legacy ambiguity is held for operator review, never guessed.
    """
    current = _card_funding_row(txn.reference)
    meta = dict(current.meta or {})

    if current.transaction_status == Transaction.FAILED:
        return "failed"
    if current.transaction_status == Transaction.SUCCESS:
        if meta.get("card_balance_applied") is True:
            return "success"
        # An old Successful row has no durable proof of whether the projection
        # increment ran.  Applying it now could double the displayed balance.
        meta["card_balance_review"] = True
        meta["reconcile"] = True
        current.meta = meta
        current.save(update_fields=["meta"])
        return "quarantined"

    if result.get("success"):
        if meta.get("card_balance_applied") is not False:
            meta["card_balance_review"] = True
            meta["reconcile"] = True
            current.meta = meta
            current.save(update_fields=["meta"])
            return "quarantined"
        card = _locked_card(current)
        card.balance += current.amount
        card.save(update_fields=["balance"])
        meta.update(_provider_meta(result))
        meta["card_balance_applied"] = True
        meta.pop("card_balance_review", None)
        meta.pop("reconcile", None)
        current.meta = meta
        current.transaction_status = Transaction.SUCCESS
        current.save(update_fields=["transaction_status", "meta"])
        txn.transaction_status = Transaction.SUCCESS
        txn.meta = meta
        return "success"

    if result.get("pending"):
        meta.update(_provider_meta(result))
        meta["reconcile"] = True
        current.meta = meta
        current.save(update_fields=["meta"])
        txn.meta = meta
        return "pending"

    wallet = Wallet.objects.select_for_update().get(user=current.user)
    wallet.balance += current.amount
    wallet.save(update_fields=["balance", "updated"])
    meta.update(_provider_meta(result))
    meta.pop("reconcile", None)
    meta["failure"] = str(result.get("message") or "Card funding failed")[:300]
    current.meta = meta
    current.transaction_status = Transaction.FAILED
    current.save(update_fields=["transaction_status", "meta"])
    txn.transaction_status = Transaction.FAILED
    txn.meta = meta
    return "failed"


@db_transaction.atomic
def resolve_card_funding(reference: str, *, disposition: str, reason: str,
                         actor, approval_id: int) -> dict:
    """Resolve an ambiguous card load after two operators verify issuer evidence."""
    disposition = str(disposition or "").strip()
    reason = str(reason or "").strip()
    if disposition not in CARD_FUNDING_DISPOSITIONS:
        raise ValueError("Invalid card-funding disposition")
    if len(reason) < 12:
        raise ValueError("Resolution reason must be at least 12 characters")
    if not isinstance(approval_id, int) or approval_id <= 0:
        raise ValueError("A valid approval id is required")

    txn = _card_funding_row(reference)
    meta = dict(txn.meta or {})
    pending_load = (txn.transaction_status == Transaction.PENDING
                    and bool(meta.get("reconcile")))
    projection_review = (txn.transaction_status == Transaction.SUCCESS
                         and meta.get("card_balance_review") is True
                         and meta.get("card_balance_applied") is not True)
    if not pending_load and not projection_review:
        raise ValueError("This card funding is no longer awaiting resolution")

    movement = Decimal("0")
    if disposition in {"confirm_loaded", "confirm_failed"} and not pending_load:
        raise ValueError("Choose a card-projection disposition for this legacy success")
    if disposition in {"confirm_projection_applied", "apply_missing_projection"} \
            and not projection_review:
        raise ValueError("Choose a funding-outcome disposition for this pending load")

    if disposition == "confirm_loaded":
        if meta.get("card_balance_applied") is not False:
            raise ValueError("Card-balance application state is ambiguous")
        card = _locked_card(txn)
        card.balance += txn.amount
        card.save(update_fields=["balance"])
        meta["card_balance_applied"] = True
        txn.transaction_status = Transaction.SUCCESS
        movement = txn.amount
    elif disposition == "confirm_failed":
        wallet = Wallet.objects.select_for_update().get(user=txn.user)
        wallet.balance += txn.amount
        wallet.save(update_fields=["balance", "updated"])
        txn.transaction_status = Transaction.FAILED
    elif disposition == "confirm_projection_applied":
        # Issuer and historical local evidence confirm old code already updated
        # the projection. Record the durable marker without moving money again.
        meta["card_balance_applied"] = True
    else:  # apply_missing_projection
        card = _locked_card(txn)
        card.balance += txn.amount
        card.save(update_fields=["balance"])
        meta["card_balance_applied"] = True
        movement = txn.amount

    actor_label = (getattr(actor, "email", "") or getattr(actor, "username", "")
                   or str(getattr(actor, "pk", actor)))
    meta.pop("reconcile", None)
    meta.pop("card_balance_review", None)
    meta["card_funding_resolution"] = {
        "disposition": disposition,
        "reason": reason[:300],
        "actor": actor_label,
        "approval_id": approval_id,
    }
    txn.meta = meta
    txn.save(update_fields=["transaction_status", "meta"])
    return {
        "reference": txn.reference,
        "status": txn.transaction_status,
        "disposition": disposition,
        "card_balance_movement": str(movement),
    }
