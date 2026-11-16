"""VTU service endpoints: airtime, data, cable, electricity.

Each purchase: verify PIN -> atomically debit wallet (PENDING row) -> call the
aggregator -> mark the row Successful, or refund on failure.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
import re

from django.utils import timezone

from common.http import (
    MIN_AIRTIME, MIN_ELECTRICITY, api, check_confirmed_price, check_daily_limit, check_send_limits, fail,
    idempotent_replay, ok, parse_amount, provider_purchase_response, require_user,
    spend_key, verify_transaction_pin,
)
from common.phones import normalize_nigerian_mobile
from common.ratelimit import ratelimit
from wallet.models import Transaction
from wallet.services import DuplicateTransaction, InsufficientFunds, LimitExceeded, existing_for_key, run_provider_purchase

from .models import CablePlan, DataPlan
from .providers import (
    bill_charge,
    remita_pay,
    remita_validate,
    vas_can_settle,
    vtu_purchase,
    vtu_verify_customer,
)

NETWORK_NAMES = {"1": "MTN", "2": "GLO", "3": "Airtel", "4": "9mobile"}
CABLE_NAMES = {"1": "GoTV", "2": "DSTV", "3": "StarTimes"}
DISCO_NAMES = {
    "1": "Ikeja", "2": "Eko", "3": "Abuja", "4": "Kano", "5": "Port Harcourt",
    "6": "Jos", "7": "Kaduna", "8": "Enugu", "9": "Ibadan",
}


def _amount(value):
    # Finite, positive, 2dp (rejects Infinity/1e500/junk; quantizes sub-kobo).
    return parse_amount(value)


def _identifier(value, *, minimum=6, maximum=20):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if re.fullmatch(rf"[0-9]{{{minimum},{maximum}}}", value) else None


def _check_pin(user, data):
    """PIN gate with brute-force lockout; returns an error response or None."""
    return verify_transaction_pin(user, data.get("transaction_pin"))


def _required_spend_key(data, user, *parts):
    raw = data.get("idempotency_key")
    if not isinstance(raw, str) or not raw.strip():
        return None, fail(
            "A stable idempotency key is required for this payment",
            status=400,
            code="idempotency_key_required",
        )
    return spend_key(raw, user, *parts), None


def _run_purchase(user, amount, service, meta, provider_call, idempotency_key=""):
    """Debit -> provider -> settle, mapping insufficient funds to a 402.

    With an idempotency key, a retried/raced request replays the original
    outcome instead of debiting and re-calling the provider. Returns
    (status, txn, result) from run_provider_purchase, or a response (fail/replay).
    """
    replay = idempotent_replay(existing_for_key(user, idempotency_key))
    if replay:
        return replay
    # Per-txn tier ceiling + large-txn face gate + fraud VELOCITY brake — the same
    # guard every other spend flow (transfers/betting/exams/cards) runs. Without
    # it, VTU was the one category not velocity-guarded, so a stolen session+PIN
    # could cash out via rapid airtime buys at a rate transfers would throttle.
    limit_err = check_send_limits(user, amount, biller=True)
    if limit_err:
        return limit_err
    # Daily bill cap (after replay so a retried purchase replays cleanly).
    daily_err = check_daily_limit(user, amount, "bill")
    if daily_err:
        return daily_err
    try:
        return run_provider_purchase(user, amount, service, meta, provider_call,
                                     idempotency_key=idempotency_key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(user, idempotency_key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    except LimitExceeded as exc:
        return fail(str(exc), status=403, code="limit_exceeded")


# ---------------- AIRTIME ----------------
@api
@require_user
def buyairtime(request):
    user, data = request.user_obj, request.data
    amount = _amount(data.get("amount"))
    if amount is None or amount < 50:
        return fail("Enter a valid amount")
    net = str(data.get("network", ""))
    if net not in NETWORK_NAMES:
        return fail("Select a valid mobile network")
    phone = normalize_nigerian_mobile(data.get("phone", ""))
    if phone is None:
        return fail("Enter a valid Nigerian mobile number")
    key, key_error = _required_spend_key(
        data, user, "airtime", net, phone, amount)
    if key_error:
        return key_error
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay
    err = _check_pin(user, data)
    if err:
        return err
    # The locked ledger debit binds its funding source. Derive it from the
    # reference so a migrated customer never spends an old attached NUBAN.
    outcome = _run_purchase(
        user, amount, f"Airtime — {NETWORK_NAMES.get(net, net)}",
        {"phone": phone, "network": net},
        lambda ref: vtu_purchase(f"{NETWORK_NAMES[net].lower()}-airtime",
                                 {"amount": str(amount), "phone": phone},
                                 reference=ref),
        idempotency_key=key,
    )
    if not isinstance(outcome, tuple):
        return outcome
    return provider_purchase_response(*outcome, success_message="Airtime purchase successful")


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
    net = str(data.get("datanetwork", ""))
    if net not in NETWORK_NAMES:
        return fail("Select a valid mobile network")
    plan_code = str(data.get("selectedDataPlan", ""))
    phone = normalize_nigerian_mobile(data.get("phone", ""))
    if phone is None:
        return fail("Enter a valid Nigerian mobile number")
    key, key_error = _required_spend_key(
        data, user, "data", net, phone, plan_code)
    if key_error:
        return key_error
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay
    plan = DataPlan.objects.filter(plan_code=plan_code, network=net).first()
    if plan is None or not plan.active:
        return fail("Plan not found", status=404)
    price_error = check_confirmed_price(data, plan.price)
    if price_error is not None:
        return price_error
    err = _check_pin(user, data)
    if err:
        return err
    outcome = _run_purchase(
        user, plan.price, f"Data — {NETWORK_NAMES.get(net, net)} {plan.name}",
        {"phone": phone, "network": net, "plan_code": plan.plan_code},
        lambda ref: vtu_purchase(f"{NETWORK_NAMES[net].lower()}-data",
                                 {"billersCode": phone, "variation_code": plan.plan_code, "phone": phone}, reference=ref),
        idempotency_key=key,
    )
    if not isinstance(outcome, tuple):
        return outcome
    return provider_purchase_response(*outcome, success_message="Data purchase successful")


# ---------------- CABLE ----------------
@api
def get_cable_plans(request):
    prov = str(request.data.get("cablenetwork", ""))
    plans = CablePlan.available_for_provider(prov)
    return ok(cable_plans=[
        {"name": p.name, "validity": p.validity, "cable_plan_code": p.cable_plan_code, "price": str(p.price)}
        for p in plans
    ])


@api
def get_cable_plans_price(request):
    plan = CablePlan.objects.filter(
        cable_plan_code=str(request.data.get("cable_plan_code", "")), active=True,
    ).exclude(wema_code="").first()
    if plan is None:
        return fail("Plan not found", status=404)
    # The bank's charge rides on top of the package and is debited with it, so
    # the app shows it before the PIN (buycable debits price + this charge).
    return ok(cable_plans_price=str(plan.price), bank_fee=str(plan.wema_charge or 0))


@api
# Third-party PII lookups, so throttled for the same reason wallet.resolve_recipient
# is: each call discloses a NON-customer's details by number — the meter one returns
# the holder's name AND home address — which unthrottled is an enumeration oracle
# over arbitrary Nigerians, not a convenience for the person paying a bill. Each
# lookup also costs money at the VTU provider, so the cap bounds that too.
@ratelimit("validate_iuc", limit=20, window=300)
@require_user
def validate_iuc(request):
    prov = str(request.data.get("cablenetwork", ""))
    if prov not in CABLE_NAMES:
        return fail("Select a valid cable provider")
    iuc = _identifier(request.data.get("iuc", ""))
    if iuc is None:
        return fail("Enter a valid smartcard number")
    res = vtu_verify_customer(CABLE_NAMES[prov].lower(), iuc)
    if res.get("success"):
        return ok(customer_name=res.get("customer_name", ""), name=res.get("customer_name", ""))
    return fail(res.get("message", "Could not verify IUC number"), status=400)


@api
@require_user
def buycable(request):
    user, data = request.user_obj, request.data
    prov = str(data.get("cablenetwork", ""))
    if prov not in CABLE_NAMES:
        return fail("Select a valid cable provider")
    plan_code = str(data.get("selectedcablePlan", ""))
    iuc = _identifier(data.get("iuc", ""))
    if iuc is None:
        return fail("Enter a valid smartcard number")
    key, key_error = _required_spend_key(
        data, user, "cable", prov, iuc, plan_code)
    if key_error:
        return key_error
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay
    plan = CablePlan.available_for_provider(prov).filter(cable_plan_code=plan_code).first()
    if plan is None:
        return fail("Plan not found", status=404)
    price_error = check_confirmed_price(data, plan.price)
    if price_error is not None:
        return price_error
    err = _check_pin(user, data)
    if err:
        return err
    verified = vtu_verify_customer(CABLE_NAMES[prov].lower(), iuc, plan_code)
    if not verified.get("success"):
        return fail(verified.get("message", "Could not verify IUC number"), status=400)
    # The bank takes its charge from the same account on top of the package.
    charge = bill_charge(CABLE_NAMES[prov].lower(), {"variation_code": plan.cable_plan_code})
    outcome = _run_purchase(
        user, plan.price + charge, f"Cable — {CABLE_NAMES.get(prov, prov)} {plan.name}",
        {"iuc": iuc, "provider": prov, "plan_code": plan.cable_plan_code,
         "customer_name": str(verified.get("customer_name") or ""),
         "bill_amount": str(plan.price), "bank_fee": str(charge)},
        lambda ref: vtu_purchase(CABLE_NAMES[prov].lower(),
                                 {"billersCode": iuc, "variation_code": plan.cable_plan_code}, reference=ref),
        idempotency_key=key,
    )
    if not isinstance(outcome, tuple):
        return outcome
    return provider_purchase_response(*outcome, success_message="Cable subscription successful")


# ---------------- ELECTRICITY ----------------
@api
@ratelimit("validate_meter", limit=20, window=300)
@require_user
def validate_meter(request):
    disco = str(request.data.get("disco", ""))
    if disco not in DISCO_NAMES:
        return fail("Select a valid electricity provider")
    meter = _identifier(request.data.get("meter", ""))
    if meter is None:
        return fail("Enter a valid meter number")
    meter_type = request.data.get("meter_type", "prepaid")
    if meter_type not in ("prepaid", "postpaid"):
        return fail("Select prepaid or postpaid")
    res = vtu_verify_customer(f"{DISCO_NAMES[disco].lower()}-electric", meter, meter_type)
    if res.get("success"):
        address = res.get("customer_address", "")
        fee = bill_charge(f"{DISCO_NAMES[disco].lower()}-electric",
                          {"variation_code": meter_type, "amount": "1"})
        return ok(customer_name=res.get("customer_name", ""), name=res.get("customer_name", ""),
                  customer_address=address, address=address, bank_fee=str(fee))
    return fail(res.get("message", "Could not verify meter number"), status=400)


@api
@require_user
def buyelectricity(request):
    user, data = request.user_obj, request.data
    amount = _amount(data.get("amount"))
    if amount is None or amount < MIN_ELECTRICITY:
        return fail(f"Minimum amount is ₦{MIN_ELECTRICITY:,.0f}")
    disco = str(data.get("disco", ""))
    if disco not in DISCO_NAMES:
        return fail("Select a valid electricity provider")
    meter = _identifier(data.get("meter", ""))
    if meter is None:
        return fail("Enter a valid meter number")
    meter_type = data.get("meter_type", "prepaid")
    if meter_type not in ("prepaid", "postpaid"):
        return fail("Select prepaid or postpaid")
    disco_name = DISCO_NAMES.get(disco, disco)
    idempotency_key, key_error = _required_spend_key(
        data, user, "electricity", disco, meter, meter_type, amount)
    if key_error:
        return key_error
    # A connectivity retry after the provider has already accepted the payment
    # must replay from our ledger even if customer verification is temporarily
    # unavailable. Re-verifying before this lookup turned a completed purchase
    # into an apparent validation failure on retry.
    replay = idempotent_replay(existing_for_key(user, idempotency_key))
    if replay:
        return replay
    err = _check_pin(user, data)
    if err:
        return err
    verified = vtu_verify_customer(
        f"{DISCO_NAMES[disco].lower()}-electric", meter, meter_type)
    if not verified.get("success"):
        return fail(verified.get("message", "Could not verify meter number"), status=400)
    customer_name = str(verified.get("customer_name", "") or "").strip()
    customer_address = str(verified.get("customer_address", "") or "").strip()
    # The bank takes its charge from the same account on top of the bill.
    charge = bill_charge(f"{DISCO_NAMES[disco].lower()}-electric",
                         {"variation_code": meter_type, "amount": str(amount)})
    outcome = _run_purchase(
        user, amount + charge, f"Electricity — {disco_name}",
        {"meter": meter, "disco": disco, "meter_type": meter_type,
         "customer_name": customer_name, "customer": customer_name,
         "customer_address": customer_address, "address": customer_address,
         "channel": "app", "bill_amount": str(amount), "bank_fee": str(charge)},
        lambda ref: vtu_purchase(f"{DISCO_NAMES[disco].lower()}-electric",
                                 {"billersCode": meter, "variation_code": meter_type, "amount": str(amount)}, reference=ref),
        idempotency_key=idempotency_key,
    )
    if not isinstance(outcome, tuple):
        return outcome
    status, txn, result = outcome
    # Prepaid purchases return a recharge token from the aggregator (success only).
    token = (result.get("token") or result.get("provider_reference", "")) if status == "success" else ""
    return provider_purchase_response(status, txn, result,
                                      success_message="Electricity purchase successful", token=token,
                                      customer_name=customer_name,
                                      customer_address=customer_address,
                                      address=customer_address)


# ---------------- REMITA (RRR bill payment) ----------------
@api
@ratelimit("validate_rrr", limit=20, window=300)
@require_user
def validate_rrr(request):
    """POST /api/utility/validate_rrr/ {access_token, rrr} -> {success, name, amount}"""
    rrr = (request.data.get("rrr") or "").strip()
    if not rrr:
        return fail("Enter the Remita RRR")
    res = remita_validate(rrr)
    if not res.get("success"):
        return fail(res.get("message", "Could not validate this RRR"), status=400)
    amount = res.get("amount")
    return ok(success=True, name=res.get("name", ""),
              amount=str(amount) if amount is not None else "", mock=bool(res.get("mock")))


@api
@require_user
def payremita(request):
    """POST /api/utility/payremita/ {access_token, rrr, amount, transaction_pin}

    Debit the wallet and pay the Remita RRR from the user's NUBAN. A timed-out payment
    stays PENDING (ALAT has no Remita status endpoint — see wema.vas_status); it is
    never auto-refunded, so a maybe-paid bill is not double-spent."""
    user, data = request.user_obj, request.data
    rrr = (data.get("rrr") or "").strip()
    if not rrr:
        return fail("Enter the Remita RRR")
    amount = _amount(data.get("amount"))
    if amount is None or amount < 100:
        return fail("Enter a valid amount")
    key, key_error = _required_spend_key(data, user, "remita", rrr, amount)
    if key_error:
        return key_error
    replay = idempotent_replay(existing_for_key(user, key))
    if replay:
        return replay
    can_settle, _settlement_reason = vas_can_settle("remita")
    if not can_settle:
        return fail(
            "Remita payments are temporarily unavailable. You have not been charged.",
            status=422,
            code="remita_unavailable",
            not_charged=True,
        )
    err = _check_pin(user, data)
    if err:
        return err
    verified = remita_validate(rrr)
    if not verified.get("success"):
        return fail(verified.get("message", "Could not validate this RRR"), status=400)
    authoritative_amount = _amount(verified.get("amount"))
    if authoritative_amount is not None and authoritative_amount != amount:
        return fail(
            "The amount for this RRR changed. Review it again before paying.",
            status=409,
            code="rrr_amount_changed",
            amount=str(authoritative_amount),
        )
    name = user.get_full_name() or user.phone or "Zitch User"
    outcome = _run_purchase(
        user, amount, f"Remita — {rrr}",
        {"rrr": rrr, "vas_rail": "wema", "vas_type": "remita"},
        lambda ref: remita_pay(amount, ref, rrr=rrr,
                               email=user.email or "", phone=user.phone or "", name=name),
        idempotency_key=key,
    )
    if not isinstance(outcome, tuple):
        return outcome
    return provider_purchase_response(*outcome, success_message="Remita payment successful")


# ---------------- RECENTS ----------------
RECENT_DAYS = 365
RECENT_SCAN = 400
RECENT_KEEP = 12


def _text(value, limit=120):
    return str(value or "").strip()[:limit]


@api
@require_user
def bill_recents(request):
    """POST /api/utility/recents/ -> what this customer has paid for before.

    The bill screens open with the last number, meter, smartcard or betting ID
    used and list the others under it. Built only from the customer's own
    successful bill payments of the last year, newest first and without
    duplicates; a failed or still-pending attempt never becomes a default.
    """
    user = request.user_obj
    since = timezone.now() - timedelta(days=RECENT_DAYS)
    rows = (Transaction.objects
            .filter(user=user, direction=Transaction.OUT,
                    transaction_status=Transaction.SUCCESS, created__gte=since)
            .order_by("-created")
            .values_list("service", "meta", "created")[:RECENT_SCAN])
    out = {"phones": [], "meters": [], "smartcards": [], "betting": [], "exam_phones": []}
    seen = set()

    def add(kind, key, item):
        if (kind, key) in seen or len(out[kind]) >= RECENT_KEEP:
            return
        seen.add((kind, key))
        out[kind].append(item)

    for service, meta, created in rows:
        meta = meta if isinstance(meta, dict) else {}
        service = str(service or "")
        when = created.isoformat()
        if service.startswith(("Airtime", "Data")):
            phone = normalize_nigerian_mobile(meta.get("phone"))
            network = str(meta.get("network", ""))
            if phone and network in NETWORK_NAMES:
                add("phones", phone, {"phone": phone, "network": network, "last_used": when})
        elif service.startswith("Electricity"):
            meter, disco = _identifier(meta.get("meter")), str(meta.get("disco", ""))
            if meter and disco in DISCO_NAMES:
                meter_type = meta.get("meter_type") if meta.get("meter_type") in ("prepaid", "postpaid") else "prepaid"
                add("meters", (disco, meter, meter_type), {
                    "meter": meter, "disco": disco, "meter_type": meter_type,
                    "name": _text(meta.get("customer_name") or meta.get("customer")),
                    "address": _text(meta.get("customer_address") or meta.get("address"), 200),
                    "last_used": when})
        elif service.startswith("Cable"):
            iuc, provider = _identifier(meta.get("iuc")), str(meta.get("provider", ""))
            if iuc and provider in CABLE_NAMES:
                add("smartcards", (provider, iuc), {
                    "iuc": iuc, "provider": provider, "name": _text(meta.get("customer_name")),
                    "last_used": when})
        elif service.startswith("Betting funding"):
            platform, user_id = _text(meta.get("platform"), 40), _text(meta.get("user_id"), 40)
            if platform and len(user_id) >= 4:
                add("betting", (platform, user_id), {"platform": platform, "user_id": user_id, "last_used": when})
        elif service.startswith("Exam PIN"):
            phone = normalize_nigerian_mobile(meta.get("phone"))
            if phone:
                add("exam_phones", phone, {"phone": phone, "last_used": when})
    return ok(success=True, **out)
