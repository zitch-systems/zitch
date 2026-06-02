"""VTU service endpoints: airtime, data, cable, electricity.

Each purchase: verify PIN -> atomically debit wallet (PENDING row) -> call the
aggregator -> mark the row Successful, or refund on failure.
"""
from decimal import Decimal, InvalidOperation

from common.http import api, fail, ok, require_user
from wallet.models import Transaction
from wallet.services import InsufficientFunds, debit, refund

from .models import CablePlan, DataPlan
from .providers import vtu_purchase, vtu_verify_customer

NETWORK_NAMES = {"1": "MTN", "2": "GLO", "3": "Airtel", "4": "9mobile"}
CABLE_NAMES = {"1": "GoTV", "2": "DSTV", "3": "StarTimes"}
DISCO_NAMES = {
    "1": "Ikeja", "2": "Eko", "3": "Abuja", "4": "Kano", "5": "Port Harcourt",
    "6": "Jos", "7": "Kaduna", "8": "Enugu", "9": "Ibadan",
}


def _amount(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _check_pin(user, data):
    pin = (data.get("transaction_pin") or "").strip()
    if not user.transaction_pin:
        return "No transaction PIN set on this account"
    if not user.check_transaction_pin(pin):
        return "Incorrect transaction PIN"
    return None


def _run_purchase(user, amount, service, meta, provider_call):
    """Shared debit -> provider -> settle/refund wrapper."""
    try:
        txn = debit(user, amount, service, meta=meta)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    result = provider_call()
    if result.get("success"):
        txn.transaction_status = Transaction.SUCCESS
        txn.meta = {**txn.meta, **{k: v for k, v in result.items() if k != "raw"}}
        txn.save(update_fields=["transaction_status", "meta"])
        return txn, result
    refund(txn)
    return fail(result.get("message", "Transaction failed"), status=502)


# ---------------- AIRTIME ----------------
@api
@require_user
def buyairtime(request):
    user, data = request.user_obj, request.data
    err = _check_pin(user, data)
    if err:
        return fail(err, status=403)
    amount = _amount(data.get("amount"))
    if amount is None or amount < 50:
        return fail("Enter a valid amount")
    net = str(data.get("network", ""))
    phone = data.get("phone", "")
    outcome = _run_purchase(
        user, amount, f"Airtime — {NETWORK_NAMES.get(net, net)}",
        {"phone": phone, "network": net},
        lambda: vtu_purchase(f"{NETWORK_NAMES.get(net, 'mtn').lower()}-airtime",
                             {"amount": str(amount), "phone": phone}),
    )
    if isinstance(outcome, tuple):
        return ok(message="Airtime purchase successful", reference=outcome[0].reference)
    return outcome


# ---------------- DATA ----------------
@api
def get_data_plans(request):
    net = str(request.data.get("datanetwork", ""))
    plan_type = str(request.data.get("selectedPlanType", ""))
    plans = DataPlan.objects.filter(network=net, plan_type=plan_type, active=True)
    return ok(data_plans=[
        {"name": p.name, "validity": p.validity, "plan_code": p.plan_code, "price": str(p.price)}
        for p in plans
    ])


@api
def get_data_plans_price(request):
    plan = DataPlan.objects.filter(plan_code=str(request.data.get("selectedDataPlan", ""))).first()
    if plan is None:
        return fail("Plan not found", status=404)
    return ok(price=str(plan.price))


@api
@require_user
def buydata(request):
    user, data = request.user_obj, request.data
    err = _check_pin(user, data)
    if err:
        return fail(err, status=403)
    plan = DataPlan.objects.filter(plan_code=str(data.get("selectedDataPlan", ""))).first()
    if plan is None:
        return fail("Plan not found", status=404)
    net = str(data.get("datanetwork", ""))
    phone = data.get("phone", "")
    outcome = _run_purchase(
        user, plan.price, f"Data — {NETWORK_NAMES.get(net, net)} {plan.name}",
        {"phone": phone, "network": net, "plan_code": plan.plan_code},
        lambda: vtu_purchase(f"{NETWORK_NAMES.get(net, 'mtn').lower()}-data",
                             {"billersCode": phone, "variation_code": plan.plan_code, "phone": phone}),
    )
    if isinstance(outcome, tuple):
        return ok(message="Data purchase successful", reference=outcome[0].reference)
    return outcome


# ---------------- CABLE ----------------
@api
def get_cable_plans(request):
    prov = str(request.data.get("cablenetwork", ""))
    plans = CablePlan.objects.filter(provider=prov, active=True)
    return ok(cable_plans=[
        {"name": p.name, "validity": p.validity, "cable_plan_code": p.cable_plan_code, "price": str(p.price)}
        for p in plans
    ])


@api
def get_cable_plans_price(request):
    plan = CablePlan.objects.filter(cable_plan_code=str(request.data.get("cable_plan_code", ""))).first()
    if plan is None:
        return fail("Plan not found", status=404)
    return ok(cable_plans_price=str(plan.price))


@api
@require_user
def validate_iuc(request):
    prov = str(request.data.get("cablenetwork", ""))
    iuc = request.data.get("iuc", "")
    res = vtu_verify_customer(CABLE_NAMES.get(prov, "dstv").lower(), iuc)
    if res.get("success"):
        return ok(customer_name=res.get("customer_name", ""), name=res.get("customer_name", ""))
    return fail(res.get("message", "Could not verify IUC number"), status=400)


@api
@require_user
def buycable(request):
    user, data = request.user_obj, request.data
    err = _check_pin(user, data)
    if err:
        return fail(err, status=403)
    plan = CablePlan.objects.filter(cable_plan_code=str(data.get("selectedcablePlan", ""))).first()
    if plan is None:
        return fail("Plan not found", status=404)
    prov = str(data.get("cablenetwork", ""))
    iuc = data.get("iuc", "")
    outcome = _run_purchase(
        user, plan.price, f"Cable — {CABLE_NAMES.get(prov, prov)} {plan.name}",
        {"iuc": iuc, "provider": prov, "plan_code": plan.cable_plan_code},
        lambda: vtu_purchase(CABLE_NAMES.get(prov, "dstv").lower(),
                             {"billersCode": iuc, "variation_code": plan.cable_plan_code}),
    )
    if isinstance(outcome, tuple):
        return ok(message="Cable subscription successful", reference=outcome[0].reference)
    return outcome


# ---------------- ELECTRICITY ----------------
@api
@require_user
def validate_meter(request):
    disco = str(request.data.get("disco", ""))
    meter = request.data.get("meter", "")
    meter_type = request.data.get("meter_type", "prepaid")
    res = vtu_verify_customer(f"{DISCO_NAMES.get(disco, 'ikeja').lower()}-electric", meter, meter_type)
    if res.get("success"):
        return ok(customer_name=res.get("customer_name", ""), name=res.get("customer_name", ""))
    return fail(res.get("message", "Could not verify meter number"), status=400)


@api
@require_user
def buyelectricity(request):
    user, data = request.user_obj, request.data
    err = _check_pin(user, data)
    if err:
        return fail(err, status=403)
    amount = _amount(data.get("amount"))
    if amount is None or amount < 500:
        return fail("Minimum amount is ₦500")
    disco = str(data.get("disco", ""))
    meter = data.get("meter", "")
    meter_type = data.get("meter_type", "prepaid")
    outcome = _run_purchase(
        user, amount, f"Electricity — {DISCO_NAMES.get(disco, disco)}",
        {"meter": meter, "disco": disco, "meter_type": meter_type},
        lambda: vtu_purchase(f"{DISCO_NAMES.get(disco, 'ikeja').lower()}-electric",
                             {"billersCode": meter, "variation_code": meter_type, "amount": str(amount)}),
    )
    if isinstance(outcome, tuple):
        txn, result = outcome
        # Prepaid purchases return a recharge token from the aggregator.
        token = result.get("token") or result.get("provider_reference", "")
        return ok(message="Electricity purchase successful", token=token, reference=txn.reference)
    return outcome
