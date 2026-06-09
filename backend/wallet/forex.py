"""Multi-currency balances + FX conversion settlement (Fincra rail).

Corridor-aware: only currencies Fincra can actually settle get wallets and may
be converted; CNY is quote/display-only (China capital controls — §13), so a
conversion touching it is blocked with a clear message. NGN lives in the primary
Wallet; other currencies in CurrencyWallet. Conversion is atomic and the quote
is time-boxed, so a stale rate is never settled.
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction as db_transaction
from django.utils import timezone

from utility.providers import fx_execute, fx_quote

from .models import CurrencyWallet, FxQuote, Transaction, Wallet
from .services import get_or_create_wallet, make_reference

SETTLEABLE = {"NGN", "USD", "GBP", "CAD"}   # hold + convert + settle
QUOTE_ONLY = {"CNY"}                         # we can show a rate, but not settle
SUPPORTED = SETTLEABLE | QUOTE_ONLY


class FxError(Exception):
    """A conversion couldn't proceed; `message` is safe to show the user."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def currency_balance(user, ccy: str) -> Decimal:
    if ccy == "NGN":
        return get_or_create_wallet(user).balance
    cw = CurrencyWallet.objects.filter(user=user, currency=ccy).first()
    return cw.balance if cw else Decimal("0")


def all_balances(user) -> dict:
    """Funded balances by currency (NGN always present)."""
    out = {"NGN": get_or_create_wallet(user).balance}
    for cw in user.currency_wallets.all():
        if cw.balance > 0:
            out[cw.currency] = cw.balance
    return out


def _fx_margin() -> Decimal:
    from whatsapp.models import SystemSetting
    try:
        return Decimal(SystemSetting.get("fx_margin_bps", "0") or "0")
    except Exception:  # noqa: BLE001
        return Decimal("0")


def create_fx_quote(user, frm: str, to: str, sell_amount) -> FxQuote:
    """Validate the pair + funds, get a provider rate, apply the margin, and
    persist a time-boxed quote. Raises FxError on anything the user must fix."""
    frm, to = frm.upper(), to.upper()
    if frm not in SUPPORTED or to not in SUPPORTED or frm == to:
        raise FxError("Pick two different supported currencies (NGN, USD, GBP, CAD).")
    blocked = QUOTE_ONLY & {frm, to}
    if blocked:
        c = blocked.pop()
        raise FxError(f"{c} is display-only for now — we can quote it but can't settle {c} yet.")
    sell = Decimal(str(sell_amount))
    if sell <= 0:
        raise FxError("Enter a valid amount.")
    if currency_balance(user, frm) < sell:
        raise FxError(f"Insufficient {frm} balance.")

    q = fx_quote(frm, to, sell)
    if not q.get("success"):
        raise FxError(q.get("message", "Couldn't get a rate right now. Try again shortly."))
    # Our spread over the provider's mid-rate (we credit the user the lower amount).
    rate = Decimal(str(q["rate"])) * (Decimal("1") - _fx_margin() / Decimal("10000"))
    receive = (sell * rate).quantize(Decimal("0.01"))
    if receive <= 0:
        raise FxError("Amount too small to convert.")
    return FxQuote.objects.create(
        user=user, quote_ref=q["quote_ref"], from_currency=frm, to_currency=to,
        sell_amount=sell.quantize(Decimal("0.01")), receive_amount=receive, rate=rate,
        expires_at=timezone.now() + timedelta(seconds=int(q.get("ttl_seconds", 90))),
    )


def _move(user, ccy: str, delta: Decimal) -> None:
    """Adjust a locked balance by `delta` (NGN -> Wallet, else CurrencyWallet).
    Raises FxError if a debit would overdraw."""
    if ccy == "NGN":
        w = Wallet.objects.select_for_update().get(user=user)
        if w.balance + delta < 0:
            raise FxError("Insufficient NGN balance.")
        w.balance += delta
        w.save(update_fields=["balance", "updated"])
    else:
        CurrencyWallet.objects.get_or_create(user=user, currency=ccy)
        cw = CurrencyWallet.objects.select_for_update().get(user=user, currency=ccy)
        if cw.balance + delta < 0:
            raise FxError(f"Insufficient {ccy} balance.")
        cw.balance += delta
        cw.save(update_fields=["balance", "updated"])


@db_transaction.atomic
def execute_fx(user, quote_ref: str, idempotency_key: str = "") -> FxQuote:
    """Settle a quote within its TTL: debit source, credit target, write the
    ledger pair. The quote is locked + single-use, so a retry/race can't convert
    twice and an expired quote is never settled at the stale rate."""
    quote = FxQuote.objects.select_for_update().filter(quote_ref=quote_ref, user=user).first()
    if quote is None:
        raise FxError("Quote not found — please request a fresh one.")
    if quote.used:
        raise FxError("This conversion was already completed.")
    if quote.expired:
        raise FxError("This rate has expired — send the request again for a fresh quote.")

    result = fx_execute(quote_ref)
    if not result.get("success"):
        raise FxError(result.get("message", "Conversion failed at the provider."))

    _move(user, quote.from_currency, -quote.sell_amount)
    _move(user, quote.to_currency, quote.receive_amount)
    quote.used = True
    quote.save(update_fields=["used"])

    ref = make_reference("ZFX")
    label = f"Convert {quote.from_currency}→{quote.to_currency}"
    Transaction.objects.create(
        user=user, service=label, amount=quote.sell_amount, currency=quote.from_currency,
        direction=Transaction.OUT, transaction_status=Transaction.SUCCESS, reference=ref,
        idempotency_key=idempotency_key,
        meta={"to": quote.to_currency, "receive": str(quote.receive_amount), "rate": str(quote.rate)},
    )
    Transaction.objects.create(
        user=user, service=label, amount=quote.receive_amount, currency=quote.to_currency,
        direction=Transaction.IN, transaction_status=Transaction.SUCCESS, reference=f"{ref}-C",
        meta={"from": quote.from_currency, "rate": str(quote.rate)},
    )
    return quote
