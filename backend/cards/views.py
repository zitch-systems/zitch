"""Virtual card endpoints: list, create, freeze, reveal details, fund.

Issuance / freeze / detail-reveal go through the card-issuer provider layer
(mock when no key). Funding moves money from the wallet ledger onto the card.
"""
from decimal import Decimal, InvalidOperation

from common.http import api, fail, ok, require_user
from utility.providers import (
    card_secure_details,
    fund_card as issuer_fund_card,
    issue_card,
    set_card_status,
)
from wallet.models import Transaction
from wallet.services import InsufficientFunds, debit, refund

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
    result = issue_card(holder, customer_ref=str(user.id))
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
    result = set_card_status(card.card_token, active=going_active)
    if not result.get("success"):
        return fail(result.get("message", "Could not update card"), status=502)

    card.status = VirtualCard.ACTIVE if going_active else VirtualCard.FROZEN
    card.save(update_fields=["status"])
    return ok(success=True, card=_card_dict(card))


@api
@require_user
def card_details(request):
    """POST /api/cards/details/ {access_token, card_id?, transaction_pin}
    -> {success, pan, cvv, expiry, holder}

    PIN-gated one-time reveal of full card number + CVV. Never stored.
    """
    user = request.user_obj
    pin = (request.data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)
    if not user.check_transaction_pin(pin):
        return fail("Incorrect transaction PIN", status=403)

    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)

    result = card_secure_details(card.card_token)
    if not result.get("success"):
        return fail(result.get("message", "Could not fetch card details"), status=502)
    return ok(success=True, pan=result.get("pan", ""), cvv=result.get("cvv", ""),
              expiry=card.expiry, holder=card.holder)


@api
@require_user
def fund_card(request):
    """POST /api/cards/fund/ {access_token, card_id?, amount, transaction_pin}
    -> {success, card, wallet}

    Debits the wallet ledger and loads the card. Refunds on issuer failure.
    """
    user = request.user_obj
    pin = (request.data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)
    if not user.check_transaction_pin(pin):
        return fail("Incorrect transaction PIN", status=403)

    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)
    if card.frozen:
        return fail("Card is frozen", status=400)

    try:
        amount = Decimal(str(request.data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum card funding is ₦100")

    try:
        txn = debit(user, amount, "Card funding", meta={"card": card.id})
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = issuer_fund_card(card.card_token, amount)
    if not result.get("success"):
        refund(txn)
        return fail(result.get("message", "Card funding failed"), status=502)

    txn.transaction_status = Transaction.SUCCESS
    txn.save(update_fields=["transaction_status"])
    card.balance += amount
    card.save(update_fields=["balance"])

    from wallet.services import get_or_create_wallet
    wallet = get_or_create_wallet(user)
    return ok(success=True, card=_card_dict(card), wallet=str(wallet.balance), message="Card funded")
