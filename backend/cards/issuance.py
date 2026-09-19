"""Durable, at-most-once orchestration for virtual-card issuance."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from .models import CardIssuance, VirtualCard


@dataclass(frozen=True)
class IssuanceClaim:
    intent: CardIssuance | None
    call_provider: bool
    card: VirtualCard | None = None


def _key_digest(user_id, client_key: str) -> str:
    secret = str(settings.SECRET_KEY or "").encode()
    body = f"card-issuance:v1\0{user_id}\0{client_key}".encode()
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


def _claim_after_race(user, key_hash: str) -> IssuanceClaim:
    """Recover the winner when a database constraint closes a concurrent race."""
    card = VirtualCard.objects.filter(user=user).first()
    if card is not None:
        return IssuanceClaim(None, False, card)
    intent = CardIssuance.objects.filter(
        user=user,
        idempotency_key_hash=key_hash,
    ).first()
    if intent is None:
        intent = CardIssuance.objects.filter(
            user=user,
            state__in=CardIssuance.ACTIVE_STATES,
        ).first()
    if intent is None:
        raise RuntimeError("Card issuance could not be claimed safely")
    return IssuanceClaim(intent, False)


def claim_card_issuance(user, client_key: str, provider: str) -> IssuanceClaim:
    """Commit one provider-call intent before any external side effect.

    Locking the user serializes the read-before-create path on PostgreSQL.  The
    two database constraints are the final guard if another worker races before
    it can observe that lock (or on a backend with weaker row locking).
    """
    key_hash = _key_digest(user.pk, client_key)
    try:
        with transaction.atomic():
            get_user_model().objects.select_for_update().get(pk=user.pk)

            card = VirtualCard.objects.filter(user=user).first()
            if card is not None:
                return IssuanceClaim(None, False, card)

            existing = CardIssuance.objects.filter(
                user=user,
                idempotency_key_hash=key_hash,
            ).first()
            if existing is not None:
                return IssuanceClaim(existing, False, existing.card)

            active = CardIssuance.objects.filter(
                user=user,
                state__in=CardIssuance.ACTIVE_STATES,
            ).first()
            if active is not None:
                return IssuanceClaim(active, False, active.card)

            intent = CardIssuance.objects.create(
                user=user,
                idempotency_key_hash=key_hash,
                reference=f"CI-{key_hash[:32].upper()}",
                provider=str(provider or "unknown")[:20],
            )
            return IssuanceClaim(intent, True)
    except IntegrityError:
        # The failed atomic block has rolled back, so querying in a fresh
        # transaction is safe and deterministically returns the winning claim.
        return _claim_after_race(user, key_hash)


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _card_fields(result: dict) -> dict | None:
    """Accept only enough explicit issuer evidence to expose a usable card."""
    token = _text(result.get("card_token"), 80)
    last4 = _text(result.get("last4"), 4)
    expiry = _text(result.get("expiry"), 5)
    if not token or not re.fullmatch(r"\d{4}", last4):
        return None
    if not re.fullmatch(r"(?:0[1-9]|1[0-2])/\d{2}", expiry):
        return None
    return {
        "card_token": token,
        "brand": _text(result.get("brand") or "Verve", 20),
        "last4": last4,
        "expiry": expiry,
    }


@transaction.atomic
def finalize_card_issuance(intent: CardIssuance, result: dict, holder: str) -> CardIssuance:
    """Persist the terminal or ambiguous provider outcome exactly once."""
    current = (CardIssuance.objects.select_for_update().select_related("card")
               .get(pk=intent.pk))
    get_user_model().objects.select_for_update().get(pk=current.user_id)
    if current.state != CardIssuance.STARTING:
        return current

    result = result if isinstance(result, dict) else {}
    current.provider_reference = _text(result.get("provider_reference"), 100)
    current.provider_status = _text(result.get("status"), 40)
    current.message = _text(result.get("message"), 300)

    if result.get("pending") is True:
        current.state = CardIssuance.PENDING
    elif result.get("success") is True:
        fields = _card_fields(result)
        if fields is None:
            # Success without a token, last four and valid expiry cannot be
            # disproved.  It may be a real issuer-side card, so never retry it.
            current.state = CardIssuance.PENDING
            current.message = (
                "Issuer reported success without complete card evidence; "
                "manual verification is required"
            )
        elif VirtualCard.objects.filter(user_id=current.user_id).exists():
            # This should be impossible after the user lock + one-card DB
            # constraint.  Treat it as evidence of an out-of-band/concurrent
            # issuance, not permission to bind this response to an arbitrary card.
            current.state = CardIssuance.PENDING
            current.message = "A concurrent card record requires manual verification"
        else:
            card = VirtualCard.objects.create(
                user_id=current.user_id,
                holder=_text(holder, 80),
                **fields,
            )
            current.card = card
            current.state = CardIssuance.SUCCEEDED
    else:
        current.state = CardIssuance.FAILED
        if not current.message:
            current.message = "Card issuer rejected the request"

    current.save(update_fields=[
        "state",
        "provider_reference",
        "provider_status",
        "message",
        "card",
        "updated",
    ])
    return current
