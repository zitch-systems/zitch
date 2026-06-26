"""Virtual card endpoints: list, create, freeze, reveal details, fund.

Issuance / freeze / detail-reveal go through the card-issuer provider layer
(mock when no key). Funding moves money from the wallet ledger onto the card.
"""
from django.db.models import F

from common.http import (
    api,
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
    card_fund as issuer_fund_card,
    card_issue,
    card_reveal,
    card_set_status,
)
from wallet.models import Transaction
from wallet.services import DuplicateTransaction, InsufficientFunds, debit, existing_for_key, refund

from .models import VirtualCard


def _card_dict(card: VirtualCard) -> dict:
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
    }


@api
@require_user
def list_cards(request):
    """POST /api/cards/list/ {access_token} -> {cards: [...]}"""
    return ok(cards=[_card_dict(c) for c in request.user_obj.cards.all()])


@api
@require_user
def create_card(request):
    """POST /api/cards/create/ {access_token} -> {success, card}

    One active virtual card per user to start. Calls the issuer to mint it.
    """
    user = request.user_obj
    if user.cards.exists():
        return ok(success=True, card=_card_dict(user.cards.first()), message="You already have a card")

    holder = (user.get_full_name() or user.phone or "Zitch User").upper()
    result = card_issue(holder, customer_ref=str(user.id), email=user.email or "")
    if not result.get("success"):
        return fail(result.get("message", "Could not create card"), status=502)

    card = VirtualCard.objects.create(
        user=user,
        card_token=result.get("card_token", ""),
        brand=result.get("brand", "Verve"),
        last4=result.get("last4", "0000"),
        expiry=result.get("expiry", "01/29"),
        holder=holder,
    )
    return ok(success=True, card=_card_dict(card), message="Virtual card created")


@api
@require_user
def toggle_freeze(request):
    """POST /api/cards/freeze/ {access_token, card_id?} -> {success, card}"""
    user = request.user_obj
    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)

    going_active = card.frozen  # if currently frozen, we're activating
    result = card_set_status(card.card_token, active=going_active)
    if not result.get("success"):
        return fail(result.get("message", "Could not update card"), status=502)

    card.status = VirtualCard.ACTIVE if going_active else VirtualCard.FROZEN
    card.save(update_fields=["status"])
    return ok(success=True, card=_card_dict(card))


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

    result = card_reveal(card.card_token)
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
    pin_err = verify_transaction_pin(user, request.data.get("transaction_pin"))
    if pin_err:
        return pin_err

    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)
    if card.frozen:
        return fail("Card is frozen", status=400)

    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum card funding is ₦100")

    # Loading the wallet onto a card moves spendable funds out of the regulated
    # ledger, so it must respect the same KYC tier ceiling + large-transfer face
    # check the transfer endpoints enforce — otherwise it's a tier/AML bypass.
    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    key = spend_key(request.data.get("idempotency_key"), user, "card-fund", card.id, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    try:
        txn = debit(user, amount, "Card funding", meta={"card": card.id}, idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = issuer_fund_card(card.card_token, amount)
    if not result.get("success"):
        refund(txn)
        return fail(result.get("message", "Card funding failed"), status=502)

    txn.transaction_status = Transaction.SUCCESS
    txn.save(update_fields=["transaction_status"])
    # Atomic DB increment so two concurrent funds can't lose an update (which
    # would debit the wallet twice but credit the card once).
    VirtualCard.objects.filter(pk=card.pk).update(balance=F("balance") + amount)
    card.refresh_from_db()

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    return ok(success=True, card=_card_dict(card), wallet=str(wallet.balance), message="Card funded")
