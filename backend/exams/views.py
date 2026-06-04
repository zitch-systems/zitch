"""Exam PIN purchase: WAEC / NECO / JAMB / NABTEB.

Same money pattern as the utility flows: verify PIN -> debit wallet (pending) ->
call the aggregator -> settle the ledger (refund on failure).
"""
from common.http import api, fail, ok, require_user, verify_transaction_pin
from utility.providers import vtu_purchase
from wallet.models import Transaction
from wallet.services import InsufficientFunds, debit, refund

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
        txn = debit(user, amount, f"{product.name} PIN x{quantity}", meta={"exam": product.code, "phone": phone, "quantity": quantity})
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = vtu_purchase(
        product.service_id or f"{product.code}-pin",
        {"billersCode": phone, "quantity": quantity, "phone": phone},
    )
    if result.get("success"):
        pins = result.get("pins") or result.get("Pin") or []
        txn.transaction_status = Transaction.SUCCESS
        txn.meta = {**txn.meta, "pins": pins, "provider_reference": result.get("provider_reference", "")}
        txn.save(update_fields=["transaction_status", "meta"])
        return ok(success=True, message="Exam PIN purchased", pins=pins, reference=txn.reference)

    refund(txn)
    return fail(result.get("message", "Transaction failed"), status=502)
