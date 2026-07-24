import json
import logging

from django.db import IntegrityError
from django.views.decorators.csrf import csrf_exempt

from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, ok, parse_amount,
    require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import funding_initialize, funding_verify, payment_provider
from utility import wema as wema_provider

from .models import FundingIntent, Wallet
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    ensure_reserved_account,
    existing_for_key,
    get_or_create_wallet,
    make_reference,
    settle_funding,
    transfer,
    wema_account_reference,
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
    a slow provider call.) Provisioning is explicit: at BVN verification time, or via
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

    The one-step "get my account" / KYC flow: the BVN (or NIN) is handed to Wema's
    reserved-account onboarding, which validates it (CBN rules — Wema won't issue a
    dedicated account for a number that fails its own KYC) and issues the NUBAN. On
    success the user is marked KYC-verified for that identifier and their tier
    recomputed, so a single BVN both provisions the virtual wallet account AND lifts
    their limit. Only a BVN is required (NIN accepted as an alternative). Idempotent:
    returns the existing account on a repeat call.

    Note: we deliberately do NOT gate on a separate BVN details-match product here —
    gating on it would block account creation even though reserved-account onboarding
    does its own BVN check.
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

    # Wema mints the NUBAN via a BVN/NIN + OTP round-trip (the sole funding rail),
    # not a one-step reserve — start it here so the existing "Get my account" call
    # drives the flow: the client shows the OTP step and finishes on
    # /api/wallet/wema/verify-otp/ (which persists the account + lifts KYC).
    res = wema_provider.create_wallet_request(
        user.phone or "", user.email or f"{user.phone}@zitch.app", bvn=bvn, nin=nin)
    if not res.get("success"):
        return fail(res.get("message", "Couldn't start account creation"), status=502)
    return ok(success=True, otp_required=True, tracking_id=res.get("tracking_id", ""),
              otp_destination=res.get("otp_destination", user.phone or ""),
              using_bvn=using_bvn, mock=res.get("mock", False),
              message="Enter the OTP sent to your phone")


# ------------------- WEMA / ALAT wallet provisioning (OTP) -------------------
# Wema mints a dedicated NUBAN via a BVN/NIN + OTP round-trip and exposes NO
# inbound-credit webhook — deposits to the NUBAN are detected by the reconcile_wema
# poller. These three endpoints drive the OTP flow; they are gated on Wema being the
# funding rail (or configured).
def _wema_funding_enabled() -> bool:
    return (payment_provider() == "wema"
            or wema_provider.wema_live() or wema_provider.wema_simulation())


@api
@ratelimit("wema_wallet_create", limit=5, window=60)
@require_user
def wema_wallet_create(request):
    """POST /api/wallet/wema/create/ {access_token, bvn?, nin?}
    -> {success, tracking_id, otp_destination, using_bvn, message}

    Step 1: submit the BVN (or NIN); Wema sends an OTP to the customer's phone.
    The client then calls /api/wallet/wema/verify-otp/ with the code + tracking_id.
    Idempotent: returns the existing account if one is already provisioned.
    """
    if not _wema_funding_enabled():
        return fail("Bank account creation is not available right now")
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return ok(**_account_payload(wallet, already=True,
                                     message="Your account is already set up"))
    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    if len(bvn) != 11 and len(nin) != 11:
        return fail("Enter your 11-digit BVN or NIN")
    using_bvn = len(bvn) == 11
    email = user.email or f"{user.phone}@zitch.app"
    res = wema_provider.create_wallet_request(user.phone or "", email, bvn=bvn, nin=nin)
    if not res.get("success"):
        return fail(res.get("message", "Couldn't start account creation"), status=502)
    return ok(success=True, tracking_id=res.get("tracking_id", ""),
              otp_destination=res.get("otp_destination", user.phone or ""),
              using_bvn=using_bvn, mock=res.get("mock", False),
              message=res.get("message", "Enter the OTP sent to your phone"))


@api
@ratelimit("wema_wallet_verify", limit=10, window=60)
@require_user
def wema_wallet_verify_otp(request):
    """POST /api/wallet/wema/verify-otp/
       {access_token, otp, tracking_id, using_bvn?, bvn?, nin?}
    -> {success, account_number, account_name, bank_name, tier, bvn_verified, nin_verified}

    Step 2: validate the OTP, then fetch + persist the created NUBAN (marked with a
    WEMA account_reference so the reconcile poller sweeps it for deposits). If the
    identifier is echoed, the user is marked KYC-verified and their tier lifted —
    mirroring the Wema account flow.
    """
    if not _wema_funding_enabled():
        return fail("Bank account creation is not available right now")
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if wallet.account_number:
        return ok(**_account_payload(wallet, already=True))
    otp = (request.data.get("otp") or "").strip()
    tracking_id = (request.data.get("tracking_id") or "").strip()
    using_bvn = bool(request.data.get("using_bvn"))
    if not otp or not tracking_id:
        return fail("Enter the OTP sent to your phone")
    val = wema_provider.validate_wallet_otp(user.phone or "", otp, tracking_id, bvn=using_bvn)
    if not val.get("success"):
        return fail(val.get("message", "OTP verification failed"), status=502)
    acct = wema_provider.get_account_details(user.phone or "", bvn=using_bvn)
    if not acct.get("success") or not acct.get("account_number"):
        return fail(acct.get("message", "Your account is being created — try again shortly"),
                    status=502)
    # Guard the unique account_number/account_reference constraints: if Wema hands
    # back a NUBAN already owned by another wallet (provider bug / reused sandbox
    # number), fail cleanly instead of a 500.
    if Wallet.objects.filter(account_number=acct["account_number"]).exclude(pk=wallet.pk).exists():
        log.warning("wema_account_number_conflict user=%s account=%s", user.id, acct["account_number"])
        return fail("We couldn't finish setting up your account. Please contact support.", status=409)
    wallet.account_number = acct["account_number"]
    wallet.account_name = acct.get("account_name", "") or (user.get_full_name() or "").strip()
    wallet.bank_name = acct.get("bank_name", "") or "Wema Bank"
    wallet.account_reference = wema_account_reference(user)
    try:
        wallet.save(update_fields=["account_number", "account_name", "bank_name",
                                   "account_reference", "updated"])
    except IntegrityError:
        log.warning("wema_account_persist_conflict user=%s account=%s", user.id, acct["account_number"])
        return fail("We couldn't finish setting up your account. Please contact support.", status=409)
    # Lift the Post-No-Debit hold ALAT places on a new Tier-1 NUBAN — until it's
    # lifted the account can be funded but not debited, so a payout/VAS from the
    # user's own NUBAN would fail. Best-effort: the account is already usable for
    # receiving; a failure here is logged and retried on the next verify/reconcile
    # rather than blocking a successful provisioning.
    pnd = wema_provider.lift_debit_restriction(acct["account_number"], bvn=using_bvn)
    if not pnd.get("success"):
        log.warning("wema_pnd_lift_failed user=%s account=%s msg=%s",
                    user.id, acct["account_number"], pnd.get("message", ""))
    # Best-effort KYC / tier lift if the client echoed the identifier. ALAT has no
    # standalone BVN/NIN lookup, so this account-creation round-trip IS the identity
    # check: the tier is only lifted when the holder name ALAT returned name-matches
    # the user's registered name (tolerant of order/middle names), so a BVN/NIN that
    # demonstrably belongs to someone else can't lift this user's tier. The match runs
    # only against a real gateway (wema_live); a clear mismatch still provisions the
    # NUBAN (funding works) but holds the tier for review.
    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    name_ok = True
    if wema_provider.wema_live():
        name_ok = not wema_provider.holder_name_mismatch(
            user.get_full_name() or "", acct.get("account_name", ""))
    fields: list[str] = []
    if not name_ok:
        log.warning("wema_provision_name_mismatch user=%s account=%s wema_name=%r",
                    user.id, acct["account_number"], acct.get("account_name", ""))
    elif using_bvn and len(bvn) == 11 and not user.bvn_verified:
        user.set_bvn(bvn)
        user.bvn_verified = True
        fields += ["bvn_hash", "bvn_last4", "bvn_verified"]
    elif not using_bvn and len(nin) == 11 and not user.nin_verified:
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
@ratelimit("wema_wallet_resend", limit=5, window=60)
@require_user
def wema_wallet_resend_otp(request):
    """POST /api/wallet/wema/resend-otp/ {access_token, tracking_id, using_bvn?}
    -> {success, message}
    """
    if not _wema_funding_enabled():
        return fail("Bank account creation is not available right now")
    user = request.user_obj
    tracking_id = (request.data.get("tracking_id") or "").strip()
    using_bvn = bool(request.data.get("using_bvn"))
    if not tracking_id:
        return fail("Missing tracking reference")
    res = wema_provider.resend_wallet_otp(user.phone or "", tracking_id, bvn=using_bvn)
    if not res.get("success"):
        return fail(res.get("message", "Couldn't resend the OTP"), status=502)
    return ok(success=True, message=res.get("message", "OTP resent"))


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


# ----------------------- WALLET FUNDING (Wema) -----------------------
@api
@ratelimit("fund_initialize", limit=20, window=60)
@require_user
def fund_initialize(request):
    """POST /api/fund/initialize/ {access_token, amount}
    -> {success, reference, authorization_url}

    The app opens authorization_url in a browser. The wallet is credited only
    after the payment rail confirms payment (verify endpoint and/or webhook).
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
    # Scope the reference to its owner: settle_funding always credits the intent's
    # own user (never the caller), so this is an ownership/info-exposure guard rather
    # than a theft vector — but a caller has no business verifying another user's ref.
    if intent is not None and intent.user_id != request.user_obj.id:
        return fail("Reference not found", status=404)
    provider = (intent.meta or {}).get("provider", "") if intent else ""
    result = funding_verify(reference, provider=provider)
    if not result.get("success"):
        return fail(result.get("message", "Payment not successful"), status=402)

    settle_funding(reference, result.get("amount_naira"))  # idempotent
    wallet = get_or_create_wallet(request.user_obj)
    return ok(success=True, wallet=str(wallet.balance), message="Wallet funded")


# ------------------- FUND FROM ALAT ACCOUNT (Pay with Bank Account) -------------------
@api
@ratelimit("alat_fund", limit=20, window=60)
@require_user
def alat_fund_initiate(request):
    """POST /api/wallet/alat/fund/ {access_token, account_number, amount}
    -> {success, reference, message}

    Start a direct debit from the user's OWN WEMA/ALAT account (they approve it in the
    ALAT app), then poll /alat/fund/verify/ to credit the wallet once it settles."""
    user = request.user_obj
    src = "".join(ch for ch in (request.data.get("account_number") or "") if ch.isdigit())
    if len(src) != 10:
        return fail("Enter your 10-digit ALAT account number")
    amount = parse_amount(request.data.get("amount"))
    if amount is None or amount < 100:
        return fail("Minimum funding amount is ₦100")
    reference = make_reference("ZALAT")
    FundingIntent.objects.create(user=user, reference=reference, amount=amount,
                                 meta={"provider": "wema_pwba"})
    res = wema_provider.pwba_fund_request(amount, reference, source_account=src,
                                          narration=f"Zitch funding {user.phone or ''}".strip())
    if not res.get("success"):
        return fail(res.get("message", "Could not start the debit"), status=502)
    return ok(success=True, reference=reference, mock=bool(res.get("mock")),
              message="Approve the debit in your ALAT app, then verify.")


@api
@ratelimit("alat_fund_verify", limit=30, window=60)
@require_user
def alat_fund_verify(request):
    """POST /api/wallet/alat/fund/verify/ {access_token, reference}
    -> {success, wallet} or {success:false, pending:true} while awaiting approval.

    Polls the direct debit and credits the wallet exactly once on a settled debit."""
    user = request.user_obj
    reference = (request.data.get("reference") or "").strip()
    if not reference:
        return fail("Reference is required")
    intent = FundingIntent.objects.filter(reference=reference).first()
    if intent is not None and intent.user_id != user.id:
        return fail("Reference not found", status=404)
    res = wema_provider.pwba_status(reference)
    if res.get("pending"):
        return ok(success=False, pending=True,
                  message="Waiting for you to approve the debit in your ALAT app.")
    if not res.get("success"):
        return fail(res.get("message", "The debit was not completed"), status=402)
    settle_funding(reference)  # idempotent credit of the intent's amount
    wallet = get_or_create_wallet(user)
    return ok(success=True, wallet=str(wallet.balance), message="Wallet funded")


@api
@ratelimit("wema_statement", limit=20, window=60)
@require_user
def wema_statement(request):
    """POST /api/wallet/statement/ {access_token, from?, to?}
    -> {success, account_number, from_date, to_date, transactions}

    The user's Wema NUBAN bank statement (ALAT transhistoryV2) for a date range
    (defaults to the last 30 days). Distinct from the Zitch ledger history — this is
    the raw bank-account movement."""
    import re
    from datetime import timedelta

    from django.utils import timezone

    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if not wallet.account_number:
        return fail("Set up your Zitch account to view your statement", status=404)

    def _date(v, default):
        v = (v or "").strip()
        return v if re.match(r"^\d{4}-\d{2}-\d{2}$", v) else default

    today = timezone.now().date()
    date_to = _date(request.data.get("to"), today.strftime("%Y-%m-%d"))
    date_from = _date(request.data.get("from"), (today - timedelta(days=30)).strftime("%Y-%m-%d"))
    res = wema_provider.get_transactions(wallet.account_number, date_from, date_to)
    if not res.get("success"):
        return fail(res.get("message", "Couldn't fetch your statement right now"), status=502)
    rows = []
    for tx in res.get("transactions", []) or []:
        n = wema_provider.normalize_transaction(tx)
        rows.append({
            "reference": n["reference"],
            "amount": str(n["amount_naira"]) if n["amount_naira"] is not None else "",
            "is_credit": n["is_credit"], "status": n["status"],
            "narration": n["narration"], "sender": n["sender"],
            "date": tx.get("transactionDate") or tx.get("date") or "",
        })
    return ok(success=True, account_number=wallet.account_number,
              from_date=date_from, to_date=date_to, transactions=rows)


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
