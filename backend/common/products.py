"""Availability of products whose bank-backed settlement is not implemented.

Local savings, lending and FX ledgers remain useful in deliberate simulation.
They must not create claims on real customer NUBAN funds in a live deployment.
An environment flag alone cannot make these settlement implementations complete.
"""
from django.conf import settings
from utility.providers import fincra_live, mock_disabled_in_prod, payout_live


MESSAGES = {
    "savings": "Fixed savings is not available yet. Contact support about an existing plan.",
    "loans": "Loans are not available yet. Contact support about an existing loan.",
    "fx": "Currency exchange is not available yet. You can still view indicative rates.",
    "airtime_cash": "Airtime-to-cash is not available yet. You can still buy airtime.",
    "card_funding": "Card top-ups are not available yet. Contact support about an existing card.",
}


class ProductUnavailable(RuntimeError):
    pass


def product_available(product):
    if product not in MESSAGES or mock_disabled_in_prod():
        return False
    # Test fixtures can exercise local ledgers without external money. A debug
    # server configured with actual bank/FX keys must not treat real accounts as
    # simulated balances merely because DEBUG is on.
    if not getattr(settings, "TESTING", False):
        if product == "fx" and fincra_live():
            return False
        if product == "card_funding":
            from utility.providers import _card_issuer_live
            if _card_issuer_live():
                return False
        if payout_live():
            return False
    return True


def product_state(product):
    available = product_available(product)
    return {"product_available": available,
            "unavailable_message": "" if available else MESSAGES[product]}


def require_product(product):
    if not product_available(product):
        raise ProductUnavailable(MESSAGES[product])


def unavailable_response(product):
    from common.http import fail

    return fail(MESSAGES[product], status=503, code="product_unavailable",
                **product_state(product))
