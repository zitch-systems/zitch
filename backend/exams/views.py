"""Exam PIN purchase: WAEC / NECO / JAMB / NABTEB.

Same money pattern as the utility flows: verify PIN -> debit wallet (pending) ->
call the aggregator -> settle the ledger (refund on failure).
"""
from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok,
    provider_purchase_response, require_user, spend_key, verify_transaction_pin,
)
from utility.providers import vtu_purchase
from wallet.services import DuplicateTransaction, InsufficientFunds, existing_for_key, run_provider_purchase

from .models import ExamProduct


@api
def list_exams(request):
    """POST /api/exams/list/ -> {exams: [{code, name, description, price}]}"""
    exams = ExamProduct.objects.filter(active=True)
    return ok(exams=[
        {"code": e.code, "name": e.name, "description": e.description, "price": str(e.price)}
        for e in exams
    ])


@api
@require_user
def buy_exam(request):
    """POST /api/exams/buy/ {access_token, exam, quantity, phone, transaction_pin}
    -> {success, message, pins, reference}
    """
    user, data = request.user_obj, request.data

    pin_err = verify_transaction_pin(user, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    product = ExamProduct.objects.filter(code=str(data.get("exam", "")), active=True).first()
    if product is None:
        return fail("Exam product not found", status=404)

    try:
        quantity = int(data.get("quantity", 1))
    except (TypeError, ValueError):
        quantity = 1
    quantity = max(1, min(10, quantity))
    phone = data.get("phone", "")
    amount = product.price * quantity

    # Buying exam PINs spends wallet cash, so enforce the same KYC tier / large-
    # transfer face ceiling as the other money-out flows.
    limit_err = check_send_limits(user, amount)
    if limit_err:
        return limit_err

    # Idempotency: a retried / double-tapped request must not debit twice — fall
    # back to a deterministic server key when the client omits one.
    key = spend_key(data.get("idempotency_key"), user, "exam", product.code, phone, quantity)
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay

    # Daily aggregate cap (shared "non-transfer spend" bucket) — after the replay
    # check. The "Exam" label prefix is what _daily_spent matches on.
    daily_err = check_daily_limit(user, amount, "bill")
    if daily_err:
        return daily_err

    try:
        status, txn, result = run_provider_purchase(
            user, amount, f"Exam PIN · {product.name} x{quantity}",
            {"exam": product.code, "phone": phone, "quantity": quantity},
            lambda ref: vtu_purchase(product.service_id or f"{product.code}-pin",
                                     {"billersCode": phone, "quantity": quantity, "phone": phone}, reference=ref),
            idempotency_key=key,
        )
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    pins = result.get("pins") or result.get("Pin") or []
    return provider_purchase_response(status, txn, result, success_message="Exam PIN purchased", pins=pins)
