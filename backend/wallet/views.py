import json
import logging

from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok, parse_amount,
    require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import funding_initialize, funding_verify, payment_provider
from utility import kora as kora_provider
from utility import monnify as monnify_provider

from .models import FundingIntent, Wallet
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    credit_kora_virtual_account_funding,
    ensure_reserved_account,
    existing_for_key,
    get_or_create_wallet,
    make_reference,
    settle_funding,
    settle_reserved_funding,
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

    A fast, side-effect-free read of the user's dedicated funding account: it never
    calls the provider on load. (A reserve needs the raw BVN, which we never store,
    so a read-time attempt can't succeed — it would only hang the Add-money page on
    a slow Kora call.) Provisioning is explicit: at BVN verification time, or via
    /api/wallet/account/create/, both of which have the BVN in hand.
    """
    wallet = get_or_create_wallet(request.user_obj)
    return ok(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
    )


def _account_payload(wallet, **extra) -> dict:
    """The dedicated-account fields every account endpoint returns, plus extras."""
    return dict(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
        **extra,
    )


@api
@ratelimit("account_create", limit=5, window=60)
@require_user
def wallet_account_create(request):
    """POST /api/wallet/account/create/ {access_token, bvn?, nin?}
    -> {success, account_number, account_name, bank_name, bank_accounts, tier,
        bvn_verified, nin_verified}

    The one-step "get my account" / KYC flow: the BVN (or NIN) is handed to
    Kora's reserved-account onboarding, which validates it (CBN rules — Kora
    won't issue a dedicated account for a number that fails its own KYC) and issues
    the NUBAN. On success the user is marked KYC-verified for that identifier and
    their tier recomputed, so a single BVN both provisions the virtual wallet
    account AND lifts their limit. Only a BVN is required (NIN accepted as an
    alternative). Idempotent: returns the existing account on a repeat call.

    Note: we deliberately do NOT gate on the separate Kora identity-match
    product here — a contract may not have it enabled, and gating on it would block
    account creation even though reserved-account onboarding does its own BVN check.
    """
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if wallet.account_number:  # already provisioned — return it (idempotent)
        return ok(**_account_payload(
            wallet, tier=user.tier, bvn_verified=user.bvn_verified, nin_verified=user.nin_verified))

    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    if len(bvn) != 11 and len(nin) != 11:
        return fail("Enter your 11-digit BVN or NIN")
    using_bvn = len(bvn) == 11

    wallet = ensure_reserved_account(user, bvn=bvn, nin=nin)
    if not wallet.account_number:
        # Surface Kora's actual reason (also logged as kora_vba_failed) so
        # the failure is self-diagnosing in the app: "authentication failed" points
        # at the keys/KORA_SECRET_KEY, a name/BVN mismatch points at the data, and
        # "not configured" means the reserved-account product isn't enabled.
        reason = getattr(wallet, "reserve_error", "") or ""
        msg = "We couldn't create your account. Check that your BVN is correct and matches your name, then try again."
        if reason:
            msg = f"We couldn't create your account: {reason}"
        return fail(msg, status=502, reason=reason)

    # Provisioning succeeded on a verified identifier — record it as KYC and lift
    # the tier (mirrors the dedicated KYC screen) so this single step also raises
    # the user's limit. Best-effort: never fail the account response on this.
    fields: list[str] = []
    if using_bvn and not user.bvn_verified:
        user.set_bvn(bvn)
        user.bvn_verified = True
        fields += ["bvn_hash", "bvn_last4", "bvn_verified"]
    elif not using_bvn and not user.nin_verified:
        user.set_nin(nin)
        user.nin_verified = True
        fields += ["nin_hash", "nin_last4", "nin_verified"]
    if fields:
        user.recompute_tier()
        user.save(update_fields=fields + ["tier"])

    return ok(**_account_payload(
        wallet, message="Your Zitch account is ready", tier=user.tier,
        bvn_verified=user.bvn_verified, nin_verified=user.nin_verified))


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


# ----------------------- WALLET FUNDING (Kora) -----------------------
@api
@ratelimit("fund_initialize", limit=20, window=60)
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after the Kora payment rail confirms
    payment (verify endpoint and/or webhook).
    """
    user = request.user_obj
    amount = parse_amount(request.data.get("amount"))
    if amount is None:
        return fail("Enter a valid amount")
    if amount < 100:
        return fail("Minimum funding amount is ₦100")

    reference = make_reference("ZPAY")
    # Stamp the rail that started this charge so verify uses the same one even if
    # PAYMENT_PROVIDER is flipped before the user returns from checkout.
    provider = payment_provider()
    FundingIntent.objects.create(user=user, reference=reference, amount=amount,
                                 meta={"provider": provider})
    email = user.email or f"{user.phone}@zitch.app"
    name = (user.get_full_name() or user.phone or "").strip()
    result = funding_initialize(email, amount, reference, name=name)
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
    -> {success, wallet} — confirms with the rail and credits once.
    """
    reference = (request.data.get("reference") or "").strip()
    if not reference:
        return fail("Reference is required")

    # Verify against the rail that started this intent (falls back to the current
    # default when the intent or its stamp is missing).
    intent = FundingIntent.objects.filter(reference=reference).first()
    provider = (intent.meta or {}).get("provider", "") if intent else ""
    result = funding_verify(reference, provider=provider)
    if not result.get("success"):
        return fail(result.get("message", "Payment not successful"), status=402)

    settle_funding(reference, result.get("amount_naira"))  # idempotent
    wallet = get_or_create_wallet(request.user_obj)
    return ok(success=True, wallet=str(wallet.balance), message="Wallet funded")


@csrf_exempt
def fund_webhook(request):
    """POST /api/fund/webhook/ — Kora (Korapay) pay-in callback.

    Verifies x-korapay-signature (HMAC-SHA256 over the payload `data` object),
    then credits idempotently. A `charge.success` with our `reference` settles
    its FundingIntent; one carrying virtual-account details (a spontaneous
    transfer with no intent) credits via the account mapping. Always 200 on
    accepted events so Kora stops retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    try:
        event = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    signature = request.headers.get("x-korapay-signature", "")
    if not kora_provider.verify_webhook(event, signature):
        log.warning("kora_webhook_bad_signature has_header=%s body_len=%s",
                    bool(signature), len(request.body or b""))
        return fail("Invalid signature", status=401)

    etype = event.get("event", "")
    data = event.get("data", {}) or {}
    if etype == "charge.success":
        reference = data.get("reference", "") or data.get("payment_reference", "")
        amount = data.get("amount")
        # A dedicated-account transfer has no FundingIntent; settle_funding is a
        # no-op for it, so fall back to the account mapping.
        if reference and settle_funding(reference, amount) is None:
            credit_kora_virtual_account_funding(data)
        log.info("kora_webhook event=%s ref=%s amount=%s", etype, reference, amount)
    else:
        log.info("kora_webhook ignored_event=%s", etype)
    from whatsapp.ops import record_audit
    record_audit("webhook.kora", actor_type="system",
                 target=data.get("reference", ""),
                 after={"event": etype, "signature": "verified"})
    return ok(status=True)


@csrf_exempt
def monnify_fund_webhook(request):
    """POST /api/fund/monnify/webhook/ — Monnify pay-in callback.

    Verifies the `monnify-signature` header (HMAC-SHA512 of the RAW body with the
    secret key), then credits idempotently. A SUCCESSFUL_TRANSACTION for a
    RESERVED_ACCOUNT credits the wallet mapped by our accountReference (or the
    destination account number) via settle_reserved_funding; a hosted-checkout
    success settles its FundingIntent by paymentReference. Always 200 on accepted
    events so Monnify stops retrying.
    """
    if request.method != "POST":
        return fail("Method not allowed", status=405)
    raw = request.body or b""
    signature = request.headers.get("monnify-signature", "")
    if not monnify_provider.verify_webhook(raw, signature):
        log.warning("monnify_webhook_bad_signature has_header=%s body_len=%s", bool(signature), len(raw))
        return fail("Invalid signature", status=401)
    try:
        event = json.loads(raw or b"{}")
    except (ValueError, TypeError):
        return fail("Invalid payload", status=400)

    etype = event.get("eventType", "")
    ed = event.get("eventData", {}) or {}
    if etype == "SUCCESSFUL_TRANSACTION":
        product = ed.get("product", {}) or {}
        txref = ed.get("transactionReference", "")
        payref = ed.get("paymentReference", "")
        amount = ed.get("amountPaid")
        if product.get("type") == "RESERVED_ACCOUNT":
            account_ref = ed.get("accountReference", "") or product.get("reference", "")
            dest = ed.get("destinationAccountInformation", {}) or {}
            number = dest.get("accountNumber", "")
            wallet = None
            if account_ref:
                wallet = Wallet.objects.filter(account_reference=account_ref).first()
            if wallet is None and number:
                wallet = Wallet.objects.filter(account_number=number).first()
            if wallet is not None:
                settle_reserved_funding(txref or payref, amount, wallet.user)
            else:
                log.warning("monnify_funding_no_wallet account_ref=%r dest=%r ref=%s",
                            account_ref, number, txref)
        else:
            # Hosted checkout: settle the FundingIntent by our paymentReference.
            settle_funding(payref or txref, amount)
        log.info("monnify_webhook event=%s txref=%s payref=%s amount=%s", etype, txref, payref, amount)
    else:
        log.info("monnify_webhook ignored_event=%s", etype)
    from whatsapp.ops import record_audit
    record_audit("webhook.monnify", actor_type="system", target=ed.get("transactionReference", ""),
                 after={"event": etype, "signature": "verified"})
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
    if amount < 50:
        return fail("Minimum transfer is ₦50")

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
