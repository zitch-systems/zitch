"""Virtual card endpoints: list, create, freeze/unfreeze."""
from common.http import api, fail, ok, require_user

from .models import VirtualCard


def _card_dict(card: VirtualCard) -> dict:
    return {
        "id": card.id,
        "brand": card.brand,
        "last4": card.last4,
        "masked": card.masked,
        "expiry": card.expiry,
        "holder": card.holder,
        "status": card.status,
        "frozen": card.status == VirtualCard.FROZEN,
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

    One active virtual card per user to start.
    """
    user = request.user_obj
    if user.cards.exists():
        return ok(success=True, card=_card_dict(user.cards.first()), message="You already have a card")
    card = VirtualCard.issue_for(user)
    return ok(success=True, card=_card_dict(card), message="Virtual card created")


@api
@require_user
def toggle_freeze(request):
    """POST /api/cards/freeze/ {access_token, card_id?} -> {success, card}

    Toggles freeze on the user's card (defaults to their first card).
    """
    user = request.user_obj
    card_id = request.data.get("card_id")
    card = user.cards.filter(id=card_id).first() if card_id else user.cards.first()
    if card is None:
        return fail("No card found", status=404)
    card.status = VirtualCard.ACTIVE if card.status == VirtualCard.FROZEN else VirtualCard.FROZEN
    card.save(update_fields=["status"])
    return ok(success=True, card=_card_dict(card))
