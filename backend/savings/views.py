"""Fixed Save endpoints: rates, quote, lock, list."""
from decimal import Decimal

from common.http import (
    api, fail, idempotent_replay, ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from wallet.services import DuplicateTransaction, InsufficientFunds, existing_for_key, get_or_create_wallet

from .models import FixedSave
from .services import lock, settle_user_maturities


def _parse_days(value):
    """Lock period (days) as an int in the rate table, or None if not a clean
    integer (a bare ``int()`` would 500 on ``"x"``/``None``/``"30.5"``)."""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return None
    return days if days in FixedSave.RATES else None


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
    principal = parse_amount(request.data.get("amount"))
    if principal is None:
        return fail("Enter a valid amount")
    days = _parse_days(request.data.get("days", 90))
    if days is None:
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

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    principal = parse_amount(data.get("amount"))
    if principal is None:
        return fail("Enter a valid amount")
    if principal < FixedSave.MIN_PRINCIPAL:
        return fail(f"Minimum is ₦{FixedSave.MIN_PRINCIPAL:,.0f}")
    days = _parse_days(data.get("days", 90))
    if days is None:
        return fail("Invalid lock period")

    key = spend_key(data.get("idempotency_key"), user, "save", principal, days)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    try:
        plan = lock(user, principal, days, idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
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
