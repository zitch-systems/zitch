"""Airtime → Cash conversion.

The user converts unused airtime from a SIM into Zitch wallet cash at a
per-network rate. Flow: verify PIN -> validate -> credit the wallet (cash value)
and record the conversion, idempotently.

PROVIDER SEAM (verify-before-live): a real airtime-to-cash product must confirm
the airtime was actually transferred to the collection number before crediting —
otherwise it pays out for airtime it never received. `collect_airtime()` is that
hook; in MOCK mode (no provider configured) it confirms instantly so the flow is
testable offline, exactly like the VTU/payments providers elsewhere.
"""
from decimal import Decimal, InvalidOperation, ROUND_DOWN

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import transaction as db_transaction

from common.http import (
    api, fail, idempotent_replay, ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from wallet.services import DuplicateTransaction, credit, existing_for_key, make_reference

from .models import ConversionRequest

# ---------------------------------------------------------------------------
# Live currency converter (NGN -> other currencies)
#
# A read-only rate lookup the app uses to convert a Naira amount into other
# currencies at live mid-market rates. No money moves — it's a calculator. Rates
# come from a free, no-key provider (open.er-api.com, NGN base) and are cached
# briefly so we don't hit it on every keystroke / focus.
# ---------------------------------------------------------------------------
FX_API_URL = "https://open.er-api.com/v6/latest/NGN"
FX_CACHE_KEY = "fx_rates_ngn"
FX_CACHE_TTL = 600  # seconds (10 min)

# The currencies surfaced in the converter UI: (ISO code, display name, symbol).
FX_CURRENCIES = [
    ("USD", "US Dollar", "$"),
    ("GBP", "British Pound", "£"),
    ("EUR", "Euro", "€"),
]


def _fetch_fx_rates():
    """NGN-based rates, cached. Returns {'rates': {...}, 'updated': str} or None."""
    cached = cache.get(FX_CACHE_KEY)
    if cached:
        return cached
    try:
        resp = requests.get(FX_API_URL, timeout=8)
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if data.get("result") != "success":
        return None
    payload = {"rates": data.get("rates", {}), "updated": data.get("time_last_update_utc")}
    cache.set(FX_CACHE_KEY, payload, FX_CACHE_TTL)
    return payload


@api
def fx_rates(request):
    """POST /api/convert/fx/ -> live NGN->currency rates for the converter UI.

    -> {success, base: 'NGN', updated, currencies: [{code, name, symbol, rate}]}
    where `rate` is the value of ₦1 in that currency (amount_ngn * rate).
    """
    payload = _fetch_fx_rates()
    if not payload:
        return fail("Couldn't fetch live rates. Please try again shortly.", status=502)
    rates = payload["rates"]
    currencies = [
        {"code": code, "name": name, "symbol": symbol, "rate": rates[code]}
        for code, name, symbol in FX_CURRENCIES
        if rates.get(code) is not None
    ]
    return ok(success=True, base="NGN", updated=payload.get("updated"), currencies=currencies)


NETWORK_NAMES = {"1": "MTN", "2": "GLO", "3": "Airtel", "4": "9mobile"}

# Payout fraction per network (what the user receives in cash per ₦1 of airtime).
# Tunable here; surfaced to the app via /api/convert/rates/.
RATES = {
    "1": Decimal("0.80"),  # MTN
    "2": Decimal("0.75"),  # GLO
    "3": Decimal("0.80"),  # Airtel
    "4": Decimal("0.75"),  # 9mobile
}
DEFAULT_RATE = Decimal("0.75")
MIN_AIRTIME = Decimal("100")
MAX_AIRTIME = Decimal("50000")


def _amount(value):
    # Finite, positive, 2dp (rejects Infinity/1e500/junk; quantizes sub-kobo).
    return parse_amount(value)


def collect_airtime(network: str, phone: str, amount: Decimal, reference: str) -> dict:
    """Confirm the airtime was received (provider hook).

    MOCK mode confirms instantly. Wire a real airtime-collection provider here
    before go-live and gate the wallet credit on its confirmation.
    """
    return {"success": True, "mock": True, "reference": reference}


@api
def rates(request):
    """POST /api/convert/rates/ -> the per-network payout rates the UI shows."""
    return ok(rates=[
        {"network": net, "name": NETWORK_NAMES.get(net, net), "rate": str(RATES.get(net, DEFAULT_RATE)),
         "percent": int(RATES.get(net, DEFAULT_RATE) * 100)}
        for net in NETWORK_NAMES
    ], min_amount=str(MIN_AIRTIME), max_amount=str(MAX_AIRTIME))


@api
@require_user
def convert_airtime(request):
    """POST /api/convert/airtime/
    {network, phone, amount, transaction_pin, idempotency_key}
    -> {success, message, reference, payout}

    Credits the wallet the cash value of the airtime, idempotently.
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    net = str(data.get("network", ""))
    if net not in RATES:
        return fail("Select a valid network")

    phone = str(data.get("phone", "")).strip()
    if len(phone) < 10:
        return fail("Enter a valid phone number")

    airtime = _amount(data.get("amount"))
    if airtime is None or airtime < MIN_AIRTIME:
        return fail(f"Minimum airtime is ₦{MIN_AIRTIME:,.0f}")
    if airtime > MAX_AIRTIME:
        return fail(f"Maximum airtime is ₦{MAX_AIRTIME:,.0f}")

    # Fall back to a deterministic server key when the client omits one, so a
    # retried convert can't credit free cash twice for one airtime transfer.
    key = spend_key(data.get("idempotency_key"), user, "convert", net, phone, airtime)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    rate = RATES.get(net, DEFAULT_RATE)
    payout = (airtime * rate).quantize(Decimal("0.01"), rounding=ROUND_DOWN)

    reference = make_reference("ZCNV")
    collected = collect_airtime(net, phone, airtime, reference)
    # Don't mint wallet cash off a MOCK confirmation in a real deploy: the actual
    # airtime-collection provider must verify the transfer first. Until it's
    # wired, refuse the conversion in production rather than pay out for airtime we
    # never received — a free-money seam otherwise. (A real provider returns no
    # `mock` flag, so it bypasses this gate. The test runner forces DEBUG=False but
    # legitimately exercises the mock flow, so exempt it via TESTING.)
    if collected.get("mock") and not settings.DEBUG and not getattr(settings, "TESTING", False):
        return fail("Airtime-to-cash isn't available yet — please try another option.", status=503)
    if not collected.get("success"):
        return fail(collected.get("message", "Could not confirm the airtime transfer"), status=502)

    try:
        with db_transaction.atomic():
            conv = ConversionRequest.objects.create(
                user=user, network=net, phone=phone, airtime_amount=airtime,
                rate=rate, payout_amount=payout, status=ConversionRequest.SUCCESS,
                reference=reference,
            )
            txn = credit(
                user, payout, f"Airtime → Cash — {NETWORK_NAMES.get(net, net)}",
                meta={"network": net, "phone": phone, "airtime": str(airtime), "rate": str(rate)},
                reference=reference, idempotency_key=key,
            )
            conv.reference = txn.reference
            conv.save(update_fields=["reference"])
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)

    return ok(success=True, message="Airtime converted to wallet cash",
              reference=txn.reference, payout=str(payout))
