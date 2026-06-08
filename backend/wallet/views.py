import json
from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, check_send_limits, fail, idempotent_replay, ok, require_user, verify_transaction_pin,
)
from utility.providers import payment_initialize, payment_verify, payment_verify_signature

from .models import FundingIntent
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    existing_for_key,
    get_or_create_wallet,
    make_reference,
    settle_funding,
    transfer,
)


@api
@require_user
def wallet_balance(request):
    """POST /api/wallet_balance/ {access_token}
    -> {success, wallet, user_first_name, user_last_name, user_phone_number, user_email}
    """
    from accounts.views import avatar_url

    user = request.user_obj
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        wallet=str(wallet.balance),
        user_first_name=user.first_name or "",
        user_last_name=user.last_name or "",
        user_phone_number=user.phone or "",
        user_email=user.email or "",
        user_avatar=avatar_url(request, user),
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


# --------------------------- WALLET FUNDING (Monnify) ---------------------------
@api
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after Monnify confirms payment (verify endpoint and/or webhook).
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
    result = payment_initialize(user.email or f"{user.phone}@zitch.app", amount, reference)
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
    -> {success, wallet} — confirms with Monnify and credits once.
    """
    reference = (request.data.get("reference") or "").strip()
    if not reference:
        return fail("Reference is required")

    result = payment_verify(reference)
    if not result.get("success"):
        return fail(result.get("message", "Payment not successful"), status=402)

    settle_funding(reference, result.get("amount_naira"))  # idempotent
    wallet = get_or_create_wallet(request.user_obj)
    return ok(success=True, wallet=str(wallet.balance), message="Wallet funded")


@csrf_exempt
def fund_webhook(request):
    """POST /api/fund/webhook/ — Monnify server-to-server callback.

    Verifies the HMAC signature, then credits the wallet idempotently on a
    successful transaction. Always 200 on accepted events so Monnify stops
    retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    signature = request.headers.get("monnify-signature", "")
    if not payment_verify_signature(request.body, signature):
        return fail("Invalid signature", status=401)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    if event.get("eventType") == "SUCCESSFUL_TRANSACTION":
        data = event.get("eventData", {}) or {}
        reference = data.get("paymentReference", "")
        amount = data.get("amountPaid")  # Monnify reports naira
        if reference:
            settle_funding(reference, amount)
    return ok(status=True)


# --------------------------- ZITCH-TO-ZITCH TRANSFER ---------------------------
def _find_recipient(identifier: str):
    """Resolve a Zitch recipient by phone (or @tag/email)."""
    from accounts.models import User
    from django.db.models import Q

    identifier = (identifier or "").strip().lstrip("@")
    if not identifier:
        return None
    return User.objects.filter(
        Q(phone=identifier) | Q(username=identifier) | Q(email__iexact=identifier)
    ).first()


@api
@require_user
def resolve_recipient(request):
    """POST /api/transfer/resolve/ {access_token, identifier}
    -> {success, name, phone} — name confirmation before sending.
    """
    recipient = _find_recipient(request.data.get("identifier", ""))
    if recipient is None:
        return fail("No Zitch user found with that detail", status=404)
    if recipient.id == request.user_obj.id:
        return fail("You can't send money to yourself", status=400)
    name = (recipient.get_full_name() or recipient.phone or "Zitch user").strip()
    return ok(success=True, name=name, phone=recipient.phone or "")


@api
@require_user
def transfer_send(request):
    """POST /api/transfer/send/ {access_token, identifier, amount, transaction_pin, note?}
    -> {success, wallet, reference}
    """
    sender = request.user_obj
    data = request.data

    pin_err = verify_transaction_pin(sender, data.get("transaction_pin"))
    if pin_err:
        return pin_err

    try:
        amount = Decimal(str(data.get("amount")))
    except (InvalidOperation, TypeError, ValueError):
        return fail("Enter a valid amount")
    if amount < 10:
        return fail("Minimum transfer is ₦10")

    limit_err = check_send_limits(sender, amount)
    if limit_err:
        return limit_err

    recipient = _find_recipient(data.get("identifier", ""))
    if recipient is None:
        return fail("No Zitch user found with that detail", status=404)
    if recipient.id == sender.id:
        return fail("You can't send money to yourself", status=400)

    key = (data.get("idempotency_key") or "").strip()
    replay = idempotent_replay(existing_for_key(sender, key))
    if replay:
        return replay

    try:
        debit_txn, _ = transfer(sender, recipient, amount, note=data.get("note", ""), idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(sender, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    wallet = get_or_create_wallet(sender)
    return ok(success=True, wallet=str(wallet.balance), reference=debit_txn.reference, message="Money sent")
