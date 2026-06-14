"""Betting wallet funding — fund a betting account from the Zitch wallet.

Same money pattern as the utility flows: verify PIN -> debit wallet (pending) ->
call the aggregator -> settle the ledger (refund on failure).
"""
from common.http import (
    api, check_send_limits, fail, idempotent_replay, ok, parse_amount, provider_purchase_response,
    require_user, spend_key, verify_transaction_pin,
)
from utility.providers import vtu_purchase
from wallet.services import DuplicateTransaction, InsufficientFunds, existing_for_key, run_provider_purchase

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

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    platform = BettingPlatform.objects.filter(code=str(data.get("platform", "")), active=True).first()
    if platform is None:
        return fail("Betting platform not found", status=404)

    user_id = (data.get("user_id") or "").strip()
    if len(user_id) < 4:
        return fail("Enter a valid betting user ID")

    amount = parse_amount(data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding is ₦100")

    # Funding an external betting account moves spendable cash out of the wallet,
    # so it must respect the same KYC tier ceiling + large-transfer face check the
    # transfer/bill flows enforce — otherwise it's a tier/AML bypass by category.
    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    # Idempotency: a retried / double-tapped request must not debit twice — fall
    # back to a deterministic server key when the client omits one.
    key = spend_key(data.get("idempotency_key"), user, "betting", platform.code, user_id, amount)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    try:
        status, txn, result = run_provider_purchase(
            user, amount, f"{platform.name} funding",
            {"platform": platform.code, "user_id": user_id},
            lambda ref: vtu_purchase(platform.service_id or f"{platform.code}-betting",
                                     {"billersCode": user_id, "amount": str(amount)}, reference=ref),
            idempotency_key=key,
        )
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    return provider_purchase_response(status, txn, result, success_message="Betting wallet funded")
