"""Betting wallet funding — fund a betting account from the Zitch wallet.

Same money pattern as the utility flows: verify PIN -> debit wallet (pending) ->
call the aggregator -> settle the ledger (refund on failure).
"""
from decimal import Decimal, InvalidOperation

from common.http import api, fail, ok, require_user
from utility.providers import vtu_purchase
from wallet.models import Transaction
from wallet.services import InsufficientFunds, debit, refund

from .models import BettingPlatform


@api
def list_platforms(request):
    """POST /api/betting/list/ -> {platforms: [{code, name, color}]}"""
    platforms = BettingPlatform.objects.filter(active=True)
    return ok(platforms=[
        {"code": p.code, "name": p.name, "color": p.color}
        for p in platforms
    ])


@api
@require_user
def fund_betting(request):
    """POST /api/betting/fund/ {access_token, platform, user_id, amount, transaction_pin}
    -> {success, message, reference}
    """
    user, data = request.user_obj, request.data

    pin = (data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return fail("No transaction PIN set on this account", status=403)
    if not user.check_transaction_pin(pin):
        return fail("Incorrect transaction PIN", status=403)

    platform = BettingPlatform.objects.filter(code=str(data.get("platform", "")), active=True).first()
    if platform is None:
        return fail("Betting platform not found", status=404)

    user_id = (data.get("user_id") or "").strip()
    if len(user_id) < 4:
        return fail("Enter a valid betting user ID")

    try:
        amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding is ₦100")

    try:
        txn = debit(user, amount, f"{platform.name} funding", meta={"platform": platform.code, "user_id": user_id})
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = vtu_purchase(
        platform.service_id or f"{platform.code}-betting",
        {"billersCode": user_id, "amount": str(amount)},
    )
    if result.get("success"):
        txn.transaction_status = Transaction.SUCCESS
        txn.meta = {**txn.meta, "provider_reference": result.get("provider_reference", "")}
        txn.save(update_fields=["transaction_status", "meta"])
        return ok(success=True, message="Betting wallet funded", reference=txn.reference)

    refund(txn)
    return fail(result.get("message", "Transaction failed"), status=502)
