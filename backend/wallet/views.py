import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt

from common.http import api, fail, ok, require_user
from utility.providers import paystack_initialize, paystack_verify, paystack_verify_signature

from .models import FundingIntent
from .services import get_or_create_wallet, make_reference, settle_funding


@api
@require_user
def wallet_balance(request):
    """POST /api/wallet_balance/ {access_token}
    -> {success, wallet, user_first_name, user_last_name, user_phone_number, user_email}
    """
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        wallet=str(wallet.balance),
        user_first_name=user.first_name or "",
        user_last_name=user.last_name or "",
        user_phone_number=user.phone or "",
        user_email=user.email or "",
    )


@api
@require_user
def transaction_history(request):
    """POST /api/user-transaction-history/ {access_token}
    -> {status, all_site_transactions: [{service, amount, transaction_status, date}]}
    """
    user = request.user_obj
    txns = user.transactions.all()[:100]
    return ok(
        status=True,
        all_site_transactions=[
            {
                "service": t.service,
                "amount": str(t.amount),
                "transaction_status": t.transaction_status,
                "date": t.created.strftime("%Y-%m-%d %H:%M"),
                "reference": t.reference,
                "direction": t.direction,
            }
            for t in txns
        ],
    )


# --------------------------- WALLET FUNDING (Paystack) ---------------------------
@api
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after Paystack confirms payment (verify endpoint and/or webhook).
    """
    user = request.user_obj
    try:
        amount = Decimal(str(request.data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding amount is ₦100")

    reference = make_reference("ZPAY")
    FundingIntent.objects.create(user=user, reference=reference, amount=amount)
    result = paystack_initialize(user.email or f"{user.phone}@zitch.app", amount, reference)
    if not result.get("success"):
        return fail(result.get("message", "Could not start payment"), status=502)
    return ok(
        success=True,
        reference=result["reference"],
        authorization_url=result.get("authorization_url", ""),
        mock=result.get("mock", False),
    )


@api
@require_user
def fund_verify(request):
    """POST /api/fund/verify/ {access_token, reference}
    -> {success, wallet} — confirms with Paystack and credits once.
    """
    reference = (request.data.get("reference") or "").strip()
    if not reference:
        return fail("Reference is required")

    result = paystack_verify(reference)
    if not result.get("success"):
        return fail(result.get("message", "Payment not successful"), status=402)

    settle_funding(reference, result.get("amount_naira"))  # idempotent
    wallet = get_or_create_wallet(request.user_obj)
    return ok(success=True, wallet=str(wallet.balance), message="Wallet funded")


@csrf_exempt
def fund_webhook(request):
    """POST /api/fund/webhook/ — Paystack server-to-server callback.

    Verifies the HMAC signature, then credits the wallet idempotently on
    charge.success. Always 200 on accepted events so Paystack stops retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    signature = request.headers.get("x-paystack-signature", "")
    if not paystack_verify_signature(request.body, signature):
        return fail("Invalid signature", status=401)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    if event.get("event") == "charge.success":
        data = event.get("data", {}) or {}
        reference = data.get("reference", "")
        amount = (data.get("amount", 0) or 0) / 100
        if reference:
            settle_funding(reference, amount)
    return ok(status=True)
