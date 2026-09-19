"""Virtual card endpoints: list, create, freeze, reveal details, fund.

Issuance / freeze / detail-reveal go through the card-issuer provider layer
(mock when no key). Funding moves money from the wallet ledger onto the card.
"""
import logging

from common.http import (
    api,
    check_daily_limit,
    check_send_limits,
    fail,
    idempotent_replay,
    ok,
    parse_amount,
    require_user,
    spend_key,
    verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import (
    card_capabilities,
    card_fund as issuer_fund_card,
    card_issue,
    card_provider,
    card_reveal,
    card_set_status,
)
from wallet.services import (DuplicateTransaction, InsufficientFunds, LimitExceeded,
                             existing_for_key)

from .issuance import claim_card_issuance, finalize_card_issuance
from .models import CardIssuance, VirtualCard
from .services import CardFundingPending, claim_card_funding, finalize_card_funding

log = logging.getLogger("zitch")


def _provider_for_card(card: VirtualCard) -> str:
    """Keep an issued card bound to the backend that created it.

    The deployment-wide provider selector can change later.  A Wema NUBAN must
    never be sent to the generic issuer (or gain generic-only capabilities) just
    because configuration changed after issuance.
    """
    try:
        issued_by = card.issuance.provider
    except (CardIssuance.DoesNotExist, AttributeError):
        issued_by = ""
    return issued_by if issued_by in ("wema", "issuer") else card_provider()


def _card_dict(card: VirtualCard) -> dict:
    provider = _provider_for_card(card)
    return {
        "id": card.id,
        "brand": card.brand,
        "last4": card.last4,
        "masked": card.masked,
        "expiry": card.expiry,
        "holder": card.holder,
        "balance": str(card.balance),
        "status": card.status,
        "frozen": card.frozen,
        "capabilities": card_capabilities(provider),
    }


def _issuance_pending(intent: CardIssuance):
    return ok(
        pending=True,
        reference=intent.reference,
        message=("Your card request is still being verified. Do not submit another "
                 "request; contact support with this reference."),
    )


def _issuance_replay(intent: CardIssuance):
    if intent.state == CardIssuance.SUCCEEDED and intent.card_id:
        return ok(
            success=True,
            duplicate=True,
            reference=intent.reference,
            card=_card_dict(intent.card),
            message="Your card was already created",
        )
    if intent.state == CardIssuance.FAILED:
        return fail(
            intent.message or "Card creation failed; start a new request to try again",
            status=409,
            code="card_issuance_failed",
            duplicate=True,
            reference=intent.reference,
        )
    return _issuance_pending(intent)


@api
@require_user
def list_cards(request):
    """POST /api/cards/list/ {access_token} -> {cards: [...]}"""
    user = request.user_obj
    active = (user.card_issuances
              .filter(state__in=CardIssuance.ACTIVE_STATES)
              .only("reference", "state")
              .first())
    issuance = ({"pending": True, "reference": active.reference,
                 "status": "pending"} if active else None)
    return ok(cards=[_card_dict(c) for c in user.cards.all()], issuance=issuance)


@api
@require_user
def create_card(request):
    """POST /api/cards/create/ {access_token, idempotency_key} -> outcome

    One virtual card per user.  A durable intent is committed before the
    non-idempotent provider POST, so timeouts and worker crashes never trigger a
    blind second issuance.
    """
    user = request.user_obj
    raw_key = request.data.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return fail(
            "A stable idempotency key is required for card creation",
            status=400,
            code="idempotency_key_required",
        )
    client_key = raw_key.strip()
    if len(client_key) > 128:
        return fail("Idempotency key is too long", status=400,
                    code="idempotency_key_invalid")

    claim = claim_card_issuance(user, client_key, card_provider())
    if claim.card is not None:
        return ok(success=True, duplicate=True, card=_card_dict(claim.card),
                  message="You already have a card")
    if claim.intent is None:
        return fail("Card creation could not be started safely", status=409)
    if not claim.call_provider:
        return _issuance_replay(claim.intent)

    holder = (user.get_full_name() or user.phone or "Zitch User").upper()
    # Wema keys the virtual card by the user's NUBAN; the generic issuer ignores it.
    account_number = getattr(getattr(user, "wallet", None), "account_number", "") or ""
    try:
        result = card_issue(
            holder,
            customer_ref=claim.intent.reference,
            email=user.email or "",
            account_number=account_number,
            phone=user.phone or "",
        )
    except Exception:
        # The POST may have reached the issuer.  Never turn an application/provider
        # exception into permission for a second issuance call.
        log.exception("card_issuance_provider_exception reference=%s",
                      claim.intent.reference)
        result = {
            "success": False,
            "pending": True,
            "message": "Card issuer outcome is not yet confirmed",
        }

    try:
        intent = finalize_card_issuance(claim.intent, result, holder)
    except Exception:
        # The STARTING row remains durable when finalization rolls back.  That is
        # intentionally an unresolved state: retrying the provider could mint a
        # second card after a successful-but-unrecorded response.
        log.exception("card_issuance_finalize_exception reference=%s",
                      claim.intent.reference)
        return _issuance_pending(claim.intent)

    if intent.state == CardIssuance.SUCCEEDED and intent.card_id:
        return ok(success=True, reference=intent.reference,
                  card=_card_dict(intent.card), message="Virtual card created")
    if intent.state == CardIssuance.FAILED:
        return fail(intent.message or "Could not create card", status=422,
                    code="card_issuance_failed", reference=intent.reference)
    return _issuance_pending(intent)


@api
@require_user
def toggle_freeze(request):
    """POST /api/cards/freeze/ {access_token, card_id?} -> {success, card}"""
    user = request.user_obj
    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)

    provider = _provider_for_card(card)
    capabilities = card_capabilities(provider)
    going_active = card.frozen  # if currently frozen, we're activating
    if going_active and not capabilities["can_unfreeze"]:
        return fail(
            "This card was permanently blocked and cannot be reactivated.",
            status=422,
            code="card_unfreeze_unsupported",
            card=_card_dict(card),
        )

    try:
        result = card_set_status(
            card.card_token,
            active=going_active,
            provider=provider,
            masked_pan=card.masked,
        )
    except Exception:
        # A state-changing request may have reached the issuer.  Do not project
        # FROZEN/ACTIVE locally until the issuer gives terminal evidence.
        log.exception("card_status_provider_exception card_id=%s", card.id)
        result = {
            "success": False,
            "pending": True,
            "message": "Card status outcome is not yet confirmed",
        }
    if not isinstance(result, dict):
        result = {
            "success": False,
            "pending": True,
            "message": "Card status outcome is not yet confirmed",
        }
    if result.get("pending"):
        return fail(
            result.get("message") or
            "Card status outcome is not yet confirmed. Reload before taking another action.",
            status=409,
            code="card_status_pending",
            pending=True,
            card=_card_dict(card),
        )
    if not result.get("success"):
        return fail(result.get("message", "Could not update card"), status=422,
                    code="card_status_failed", card=_card_dict(card))

    card.status = VirtualCard.ACTIVE if going_active else VirtualCard.FROZEN
    card.save(update_fields=["status"])
    message = ("Card permanently blocked" if capabilities["permanent_block"]
               else ("Card unfrozen" if going_active else "Card frozen"))
    return ok(success=True, card=_card_dict(card), message=message)


@api
@ratelimit("card_details", limit=10, window=60)
@require_user
def card_details(request):
    """POST /api/cards/details/ {access_token, card_id?, transaction_pin}
    -> {success, pan, cvv, expiry, holder}

    PIN-gated one-time reveal of full card number + CVV. Never stored.
    """
    user = request.user_obj
    pin_err = verify_transaction_pin(user, request.data.get("transaction_pin"))
    if pin_err:
        return pin_err

    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)

    result = card_reveal(card.card_token, provider=_provider_for_card(card))
    if not result.get("success"):
        return fail(result.get("message", "Could not fetch card details"), status=502)
    return ok(success=True, pan=result.get("pan", ""), cvv=result.get("cvv", ""),
              expiry=card.expiry, holder=card.holder)


@api
@ratelimit("fund_card", limit=15, window=60)
@require_user
def fund_card(request):
    """POST /api/cards/fund/ {access_token, card_id?, amount, transaction_pin}
    -> {success, card, wallet}

    Debits the wallet ledger and loads the card. Refunds on issuer failure.
    """
    user = request.user_obj
    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)

    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum card funding is ₦100")

    raw_key = request.data.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return fail(
            "A stable idempotency key is required for card funding",
            status=400,
            code="idempotency_key_required",
        )
    key = spend_key(raw_key, user, "card-fund", card.id, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    provider = _provider_for_card(card)
    if not card_capabilities(provider)["can_fund"]:
        return fail(
            "Incremental funding is not supported for this card.",
            status=422,
            code="card_funding_unsupported",
            card=_card_dict(card),
        )

    # Mutable authorization/card state applies to a NEW provider call, not to
    # recovering the durable result of one already submitted under this key.
    pin_err = verify_transaction_pin(user, request.data.get("transaction_pin"))
    if pin_err:
        return pin_err
    if card.frozen:
        return fail("Card is frozen", status=400)

    # Loading the wallet onto a card moves spendable funds out of the regulated
    # ledger, so it must respect the same KYC tier ceiling + large-transfer face
    # check the transfer endpoints enforce — otherwise it's a tier/AML bypass.
    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    # Daily aggregate cap (shared "non-transfer spend" bucket) — after the replay
    # check. The "Card funding" label is what _daily_spent matches on.
    daily_err = check_daily_limit(user, amount, "bill")
    if daily_err:
        return daily_err

    try:
        txn = claim_card_funding(user, card, amount, key)
    except CardFundingPending as exc:
        return fail(
            "A previous card funding is still under review. Do not submit another load.",
            status=409,
            code="card_funding_pending",
            pending=True,
            reference=exc.reference,
        )
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    except LimitExceeded as exc:
        return fail(str(exc), status=403, code="limit_exceeded")

    try:
        result = issuer_fund_card(card.card_token, amount, provider=provider)
    except Exception:
        # The non-idempotent load may have reached the issuer. Keep the debit
        # pending for checked reconciliation instead of returning a terminal
        # error that would allow a new load.
        log.exception("card_funding_provider_exception reference=%s", txn.reference)
        result = {
            "success": False,
            "pending": True,
            "message": "Card issuer outcome is not yet confirmed",
        }
    if not isinstance(result, dict):
        log.error("card_funding_invalid_result reference=%s type=%s",
                  txn.reference, type(result).__name__)
        result = {
            "success": False,
            "pending": True,
            "message": "Card issuer outcome is not yet confirmed",
        }
    outcome = finalize_card_funding(txn, result)

    if outcome in {"pending", "quarantined"}:
        return ok(
            pending=True,
            reference=txn.reference,
            message=("Card funding is under review. Do not retry with a new request; "
                     "support must confirm the issuer outcome."),
        )
    if outcome != "success":
        # `finalize_card_funding` reaches this branch only after atomically
        # refunding a verified terminal rejection. A 5xx would make durable app
        # clients misclassify that rejection as unknown and retain the key.
        return fail(result.get("message", "Card funding failed"), status=422,
                    code="card_funding_failed", reference=txn.reference)
    card.refresh_from_db()

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    return ok(success=True, card=_card_dict(card), wallet=str(wallet.balance),
              reference=txn.reference, message="Card funded")
