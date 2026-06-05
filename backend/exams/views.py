"""Exam PIN purchase: WAEC / NECO / JAMB / NABTEB.

Same money pattern as the utility flows: verify PIN -> debit wallet (pending) ->
call the aggregator -> settle the ledger (refund on failure).
"""
from common.http import api, fail, ok, provider_purchase_response, require_user, verify_transaction_pin
from utility.providers import vtu_purchase
from wallet.services import InsufficientFunds, run_provider_purchase

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

    try:
        status, txn, result = run_provider_purchase(
            user, amount, f"{product.name} PIN x{quantity}",
            {"exam": product.code, "phone": phone, "quantity": quantity},
            lambda ref: vtu_purchase(product.service_id or f"{product.code}-pin",
                                     {"billersCode": phone, "quantity": quantity, "phone": phone}, reference=ref),
        )
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    pins = result.get("pins") or result.get("Pin") or []
    return provider_purchase_response(status, txn, result, success_message="Exam PIN purchased", pins=pins)
