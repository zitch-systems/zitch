import json
import logging

from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok, parse_amount,
    require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import payment_initialize, payment_verify, payment_verify_signature

from .models import FundingIntent
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    credit_reserved_account_funding,
    ensure_reserved_account,
    existing_for_key,
    get_or_create_wallet,
    make_reference,
    settle_funding,
    transfer,
)

log = logging.getLogger("wallet")


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
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
        user_first_name=user.first_name or "",
        user_last_name=user.last_name or "",
        user_phone_number=user.phone or "",
        user_email=user.email or "",
        user_avatar=avatar_url(request, user),
    )


@api
@require_user
def wallet_account(request):
    """POST /api/wallet/account/ {access_token}
    -> {success, account_number, account_name, bank_name, bank_accounts}

    Returns the user's dedicated funding account, reserving one on first call if
    it's missing but the user is already KYC-verified (Monnify can mint a
    dedicated account from the BVN/NIN already on file via the contract).
    """
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if not wallet.account_number and (user.bvn_verified or user.nin_verified):
        wallet = ensure_reserved_account(user)
    return ok(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
    )


@api
@ratelimit("account_create", limit=5, window=60)
@require_user
def wallet_account_create(request):
    """POST /api/wallet/account/create/ {access_token, bvn?, nin?}
    -> {success, account_number, account_name, bank_name, bank_accounts}

    Mints the user's dedicated funding account via Monnify's own onboarding:
    the BVN/NIN is handed to Monnify, which verifies it and issues the NUBAN.
    Lets a user get a funding account by entering their BVN here, without first
    completing the separate in-app (Prembly) KYC flow.
    """
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if wallet.account_number:  # already provisioned — return it (idempotent)
        return ok(
            success=True,
            account_number=wallet.account_number,
            account_name=wallet.account_name,
            bank_name=wallet.bank_name,
            bank_accounts=wallet.bank_accounts or [],
        )

    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    if len(bvn) != 11 and len(nin) != 11:
        return fail("Enter your 11-digit BVN or NIN")

    wallet = ensure_reserved_account(user, bvn=bvn, nin=nin)
    if not wallet.account_number:
        # Monnify rejected onboarding (wrong number, name mismatch, or the
        # contract has no reserved-account product). The exact reason is logged
        # server-side (monnify_reserve_failed).
        return fail(
            "We couldn't create your account. Check that your BVN/NIN is correct "
            "and matches your name, then try again.",
            status=502,
        )
    return ok(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
        message="Your Zitch account is ready",
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
@ratelimit("fund_initialize", limit=20, window=60)
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after Monnify confirms payment (verify endpoint and/or webhook).
    """
    user = request.user_obj
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
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
        # The #1 reason a real transfer never credits: Monnify is calling but the
        # hash doesn't match (wrong MONNIFY_SECRET_KEY) or no signature header.
        log.warning("monnify_webhook_bad_signature has_header=%s body_len=%s",
                    bool(signature), len(request.body or b""))
        return fail("Invalid signature", status=401)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    event_type = event.get("eventType")
    if event_type == "SUCCESSFUL_TRANSACTION":
        data = event.get("eventData", {}) or {}
        product_type = (data.get("product", {}) or {}).get("type")
        log.info("monnify_webhook event=%s product=%s txref=%s amount=%s",
                 event_type, product_type, data.get("transactionReference", ""),
                 data.get("amountPaid"))
        # A transfer into a user's dedicated (reserved) account funds the wallet
        # with no FundingIntent behind it — credit it via the account mapping.
        # A checkout/init-transaction payment settles its FundingIntent as before.
        if product_type == "RESERVED_ACCOUNT":
            credit_reserved_account_funding(data)
        else:
            reference = data.get("paymentReference", "")
            amount = data.get("amountPaid")  # Monnify reports naira
            if reference:
                settle_funding(reference, amount)
    else:
        log.info("monnify_webhook ignored_event=%s", event_type)
    from whatsapp.ops import record_audit
    record_audit("webhook.monnify", actor_type="system",
                 target=(event.get("eventData") or {}).get("paymentReference", ""),
                 after={"event": event.get("eventType", ""), "signature": "verified"})
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
@ratelimit("resolve_recipient", limit=20, window=60)
@require_user
def resolve_recipient(request):
    """POST /api/transfer/resolve/ {access_token, identifier}
    -> {success, name, phone} — name confirmation before sending.

    Rate-limited: without a throttle this is an unauthenticated-cost enumeration
    oracle that confirms whether any phone/@tag/email maps to a Zitch user and
    discloses the holder's name.
    """
    recipient = _find_recipient(request.data.get("identifier", ""))
    if recipient is None:
        return fail("No Zitch user found with that detail", status=404)
    if recipient.id == request.user_obj.id:
        return fail("You can't send money to yourself", status=400)
    name = (recipient.get_full_name() or recipient.phone or "Zitch user").strip()
    return ok(success=True, name=name, phone=recipient.phone or "")


@api
@ratelimit("transfer_send", limit=12, window=60)
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

    amount = parse_amount(data.get("amount"))
    if amount is None:
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

    key = spend_key(data.get("idempotency_key"), sender, "p2p", recipient.id, amount)
    replay = idempotent_replay(existing_for_key(sender, key))
    if replay:
        return replay

    # Daily transfer cap (after replay so a retried transfer replays cleanly).
    daily_err = check_daily_limit(sender, amount, "transfer")
    if daily_err:
        return daily_err

    try:
        debit_txn, _ = transfer(sender, recipient, amount, note=data.get("note", ""), idempotency_key=key)
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(sender, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)

    wallet = get_or_create_wallet(sender)
    return ok(success=True, wallet=str(wallet.balance), reference=debit_txn.reference, message="Money sent")
