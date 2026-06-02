"""Fixed Save endpoints: rates, quote, lock, list."""
from decimal import Decimal, InvalidOperation

from common.http import api, fail, ok, require_user
from wallet.services import InsufficientFunds, get_or_create_wallet

from .models import FixedSave
from .services import lock, settle_user_maturities


def _plan_dict(p: FixedSave) -> dict:
    return {
        "reference": p.reference,
        "principal": str(p.principal),
        "interest": str(p.interest),
        "rate": str(p.rate),
        "duration_days": p.duration_days,
        "maturity_value": str(p.maturity_value),
        "status": p.status,
        "matures_at": p.matures_at.strftime("%Y-%m-%d"),
    }


@api
def savings_rates(request):
    """POST /api/savings/rates/ -> {rates: [{days, rate}], min}"""
    return ok(
        rates=[{"days": d, "rate": str(r)} for d, r in sorted(FixedSave.RATES.items())],
        min=str(FixedSave.MIN_PRINCIPAL),
    )


@api
@require_user
def savings_quote(request):
    """POST /api/savings/quote/ {access_token, amount, days}
    -> {principal, interest, maturity_value, rate, days}
    """
    try:
        principal = Decimal(str(request.data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    days = int(request.data.get("days", 90))
    if days not in FixedSave.RATES:
        return fail("Invalid lock period")
    interest = FixedSave.quote(principal, days)
    return ok(
        principal=str(principal),
        interest=str(interest),
        maturity_value=str(principal + interest),
        rate=str(FixedSave.RATES[days]),
        days=days,
    )


@api
@require_user
def savings_create(request):
    """POST /api/savings/create/ {access_token, amount, days, transaction_pin}
    -> {success, wallet, plan}
    """
    user, data = request.user_obj, request.data

    pin = (data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)
    if not user.check_transaction_pin(pin):
        return fail("Incorrect transaction PIN", status=403)

    try:
        principal = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if principal < FixedSave.MIN_PRINCIPAL:
        return fail(f"Minimum is ₦{FixedSave.MIN_PRINCIPAL:,.0f}")
    days = int(data.get("days", 90))
    if days not in FixedSave.RATES:
        return fail("Invalid lock period")

    try:
        plan = lock(user, principal, days)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    wallet = get_or_create_wallet(user)
    return ok(success=True, wallet=str(wallet.balance), plan=_plan_dict(plan), message="Savings locked")


@api
@require_user
def savings_list(request):
    """POST /api/savings/list/ {access_token}
    -> {total_locked, plans: [...]}
    """
    user = request.user_obj
    settle_user_maturities(user)  # pay out anything that matured since the last visit
    plans = user.savings.all()
    total = sum((p.principal for p in plans if p.status == FixedSave.ACTIVE), Decimal("0"))
    return ok(total_locked=str(total), plans=[_plan_dict(p) for p in plans])
