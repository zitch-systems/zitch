import hmac
import json
import logging
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction as db_transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from accounts.models import hash_identifier
from common.http import (
    api, check_daily_limit, check_send_limits, fail, idempotent_replay, mask_pii, ok,
    parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import funding_initialize, funding_verify, payment_provider
from utility import wema as wema_provider

from .models import FundingIntent, Wallet, WemaProvisioningAttempt
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    ensure_reserved_account,
    existing_for_key,
    get_or_create_wallet,
    make_reference,
    provision_wema_account,
    settle_funding,
    sync_bank_tier,
    settle_reserved_funding,
    transfer,
)

log = logging.getLogger("wallet")
User = get_user_model()
WEMA_ATTEMPT_TTL = timedelta(minutes=15)


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
        bank_tier=wallet.bank_tier,
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
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    return ok(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
        bank_tier=wallet.bank_tier,
        # The customer's registered legal name, so the Add-money screen can always
        # show whose account this is — even before it's provisioned, or on the rare
        # provider response that omits the holder name (account_name is blank).
        holder_name=(user.get_full_name() or "").strip(),
    )


def _account_payload(wallet, **extra) -> dict:
    """The dedicated-account fields every account endpoint returns, plus extras."""
    return dict(
        success=True,
        account_number=wallet.account_number,
        account_name=wallet.account_name,
        bank_name=wallet.bank_name,
        bank_accounts=wallet.bank_accounts or [],
        bank_tier=wallet.bank_tier,
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
    res, identity_error = _start_wema_attempt(user, bvn, nin)
    if identity_error:
        return fail(identity_error, status=409)
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


def _identity_for_attempt(bvn: str, nin: str) -> tuple[str, str]:
    """Return the single identity type/value accepted for wallet onboarding."""
    if len(bvn) == 11:
        return WemaProvisioningAttempt.BVN, bvn
    return WemaProvisioningAttempt.NIN, nin


def _identity_owned_by_another_user(user, identity_type: str, raw: str) -> bool:
    field = "bvn_hash" if identity_type == WemaProvisioningAttempt.BVN else "nin_hash"
    return User.objects.exclude(pk=user.pk).filter(**{field: hash_identifier(raw)}).exists()


def _record_wema_attempt(user, tracking_id: str, identity_type: str,
                         raw_identity: str) -> WemaProvisioningAttempt:
    """Persist the identity binding after Wema accepts initiation; no raw ID."""
    now = timezone.now()
    WemaProvisioningAttempt.objects.filter(
        user=user, status=WemaProvisioningAttempt.PENDING,
    ).exclude(tracking_id=tracking_id).update(status=WemaProvisioningAttempt.FAILED)
    attempt, _ = WemaProvisioningAttempt.objects.update_or_create(
        user=user, tracking_id=tracking_id,
        defaults={
            "identity_type": identity_type,
            "identity_hash": hash_identifier(raw_identity),
            "identity_last4": raw_identity[-4:],
            "status": WemaProvisioningAttempt.PENDING,
            "expires_at": now + WEMA_ATTEMPT_TTL,
        },
    )
    return attempt


# The gateway's way of saying "this customer is already onboarded". Matched on the
# durable part of the wording rather than the whole string, which varies by product
# and is brand-stripped by _msg before it reaches us.
_ALREADY_ONBOARDED = re.compile(r"already\s+exist", re.I)


def _adopt_existing_wema_account(user, *, using_bvn: bool, reason: str) -> dict | None:
    """Attach the NUBAN the bank ALREADY holds for this customer. None if there isn't one.

    Creation is refused once the bank has a customer record, so a user in that state
    can never finish setup by retrying — the account must be read back instead. It is
    looked up by the user's OWN phone number (the same key creation would have used),
    so this adopts that customer's account and no one else's.

    Deliberately does NOT touch the KYC tier or mark the BVN/NIN verified: the OTP
    round-trip is what attests the identity, and this path has no OTP. The user gets
    a working funding account; lifting the tier still requires the normal flow.
    """
    if not _ALREADY_ONBOARDED.search(reason or ""):
        return None
    acct = wema_provider.get_account_details(user.phone or "", bvn=using_bvn)
    number = str(acct.get("account_number") or "").strip()
    if not acct.get("success") or not number:
        return None
    wallet, outcome = provision_wema_account(
        user, account_number=number, account_name=acct.get("account_name", ""),
        bank_name=acct.get("bank_name", ""), source="adopt-existing")
    if outcome.startswith("conflict"):
        log.warning("wema_adopt_conflict user=%s outcome=%s", user.id, outcome)
        return None
    # A partnership NUBAN is created under a Post-No-Debit hold, so an adopted one
    # may still be carrying it — and the payout debits this very account. Best-effort,
    # exactly as the OTP path treats it.
    pnd = wema_provider.lift_debit_restriction(number, bvn=using_bvn)
    if not pnd.get("success"):
        log.warning("wema_pnd_lift_failed user=%s account=%s msg=%s",
                    user.id, number, pnd.get("message", ""))
    log.info("wema_adopted_existing_account user=%s outcome=%s", user.id, outcome)
    return _account_payload(
        wallet, already=True,
        message="Your bank account was already set up — we've reconnected it.")


def _start_wema_attempt(user, bvn: str, nin: str) -> tuple[dict | None, str | None]:
    """Start and bind an OTP request, returning (provider_result, error)."""
    identity_type, raw_identity = _identity_for_attempt(bvn, nin)
    if _identity_owned_by_another_user(user, identity_type, raw_identity):
        return None, "This identity is already linked to another account. Contact support if this is unexpected."
    # Wema explicitly forbids reusing customer identity fields across creation
    # requests. If this user restarts the screen while a valid attempt exists,
    # return that opaque tracking reference and use the dedicated resend endpoint;
    # do not submit the BVN/NIN to account creation a second time.
    existing = WemaProvisioningAttempt.objects.filter(
        user=user,
        identity_type=identity_type,
        identity_hash=hash_identifier(raw_identity),
        status=WemaProvisioningAttempt.PENDING,
        expires_at__gt=timezone.now(),
    ).order_by("-created").first()
    if existing is not None:
        return {
            "success": True,
            "tracking_id": existing.tracking_id,
            "otp_destination": user.phone or "",
            "reused": True,
        }, None
    email = user.email or f"{user.phone}@zitch.app"
    res = wema_provider.create_wallet_request(user.phone or "", email, bvn=bvn, nin=nin)
    if not res.get("success"):
        return res, None
    tracking_id = str(res.get("tracking_id") or "").strip()
    if not tracking_id:
        return {"success": False, "message": "Couldn't start account creation"}, None
    _record_wema_attempt(user, tracking_id, identity_type, raw_identity)
    return res, None


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
    res, identity_error = _start_wema_attempt(user, bvn, nin)
    if identity_error:
        return fail(identity_error, status=409)
    if not res.get("success"):
        # "Customer records already exist": the bank already holds an account for
        # this customer, so there is nothing to create — it has to be FETCHED. Without
        # this the flow dead-ends for good, since every retry asks to create the same
        # customer again and is refused for the same reason. Reachable whenever the
        # NUBAN is missing on our side but present on theirs: a wallet whose
        # test-mode account number was cleared, a half-finished earlier setup, or a
        # provisioning callback we never received.
        recovered = _adopt_existing_wema_account(user, using_bvn=using_bvn,
                                                 reason=res.get("message", ""))
        if recovered is not None:
            return ok(**recovered)
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
    WEMA account_reference so the reconcile poller sweeps it for deposits). The
    identity and its type come from the server-side initiation record, never from
    client claims in this second request.
    """
    if not _wema_funding_enabled():
        return fail("Bank account creation is not available right now")
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    # NOTE: no early return when a NUBAN already exists. The bank's Account Creation
    # callback can provision it before the customer finishes the OTP step, and an
    # early return here would skip the KYC/tier block below — leaving them with a
    # working account permanently stuck at the tier-0 limit. Provisioning is skipped
    # when it is already done; identity verification still runs.
    already = bool(wallet.account_number)
    otp = (request.data.get("otp") or "").strip()
    tracking_id = (request.data.get("tracking_id") or "").strip()
    if not otp or not tracking_id:
        return fail("Enter the OTP sent to your phone")
    attempt = WemaProvisioningAttempt.objects.filter(
        user=user, tracking_id=tracking_id, status=WemaProvisioningAttempt.PENDING,
    ).first()
    if attempt is None or attempt.expired:
        return fail("This verification request has expired. Start account setup again.", status=400)
    using_bvn = attempt.identity_type == WemaProvisioningAttempt.BVN
    # Older clients echo the raw value. It is not required, but if present it must
    # match the initiation record so tampering is rejected loudly rather than ignored.
    echoed = "".join(ch for ch in (
        request.data.get("bvn" if using_bvn else "nin") or ""
    ) if ch.isdigit())
    if echoed and not hmac.compare_digest(hash_identifier(echoed), attempt.identity_hash):
        return fail("Identity details do not match this verification request.", status=400)
    val = wema_provider.validate_wallet_otp(user.phone or "", otp, tracking_id, bvn=using_bvn)
    if not val.get("success"):
        return fail(val.get("message", "OTP verification failed"), status=502)
    if already:
        # Provisioned already (by an earlier verify, or by the bank's Account Creation
        # callback). Skip the provisioning write. The holder name is NOT read back from
        # the wallet here — it is resolved from the bank in the name-match block below,
        # for the reason spelled out there.
        holder_name = None
    else:
        acct = wema_provider.get_account_details(user.phone or "", bvn=using_bvn)
        if not acct.get("success") or not acct.get("account_number"):
            return fail(acct.get("message", "Your account is being created — try again shortly"),
                        status=502)
        wallet, outcome = provision_wema_account(
            user, account_number=acct["account_number"],
            account_name=acct.get("account_name", ""), bank_name=acct.get("bank_name", ""),
            source="otp")
        if outcome.startswith("conflict"):
            log.warning("wema_account_conflict user=%s account=%s outcome=%s",
                        user.id, acct["account_number"], outcome)
            return fail("We couldn't finish setting up your account. Please contact support.",
                        status=409)
        # Lift the Post-No-Debit hold ALAT places on a new Tier-1 NUBAN — until it's
        # lifted the account can be funded but not debited, so a payout/VAS from the
        # user's own NUBAN would fail. Best-effort: the account is already usable for
        # receiving; a failure here is logged and retried on the next verify/reconcile
        # rather than blocking a successful provisioning.
        pnd = wema_provider.lift_debit_restriction(acct["account_number"], bvn=using_bvn)
        if not pnd.get("success"):
            log.warning("wema_pnd_lift_failed user=%s account=%s msg=%s",
                        user.id, acct["account_number"], pnd.get("message", ""))
        holder_name = acct.get("account_name", "")
    # Best-effort KYC / tier lift from the server-bound identifier. ALAT has no
    # standalone BVN/NIN lookup, so this account-creation round-trip IS the identity
    # check: the tier is only lifted when the holder name ALAT returned name-matches
    # the user's registered name (tolerant of order/middle names), so a BVN/NIN that
    # demonstrably belongs to someone else can't lift this user's tier. The match runs
    # only against a real gateway (wema_live); a clear mismatch still provisions the
    # NUBAN (funding works) but holds the tier for review.
    name_ok = True
    if wema_provider.wema_live():
        if holder_name is None:
            # Ask the bank what name it holds against this NUBAN.
            #
            # wallet.account_name cannot answer that question, though it looks like it
            # can. provision_wema_account substitutes the user's OWN registered name
            # whenever the bank hands over a blank one — deliberately, because a
            # funding account with no name can't be safely paid into — and the Account
            # Creation callback's nubanName is routinely blank. Matching that stored
            # value against the registered name compares it to itself and passes every
            # single time. For every account the bank's callback provisioned before the
            # customer reached this step (the common ordering, which is why the early
            # return above was removed), the only identity check in the ALAT flow was a
            # rubber stamp: submit anyone's BVN, get the tier.
            #
            # The account number is the discriminator; the name we happen to have
            # stored is not. get_kyc_status is keyed by NUBAN and returns the bank's
            # own accountName, which is real evidence.
            status = wema_provider.get_kyc_status(wallet.account_number)
            holder_name = str(status.get("name") or "") if status.get("success") else ""
            if not holder_name:
                # Unreadable bank data cannot prove identity. Provisioning may still
                # complete, but the KYC tier stays held for review.
                log.warning("wema_holder_name_unavailable user=%s account=%s",
                            user.id, wallet.account_number)
        name_ok = not wema_provider.holder_name_mismatch(
            user.get_full_name() or "", holder_name)
    fields: list[str] = []
    if not name_ok:
        log.warning("wema_provision_name_mismatch user=%s account=%s wema_name=%r",
                    user.id, wallet.account_number, holder_name)
    elif using_bvn and not user.bvn_verified:
        user.bvn_hash = attempt.identity_hash
        user.bvn_last4 = attempt.identity_last4
        user.bvn_verified = True
        fields += ["bvn_hash", "bvn_last4", "bvn_verified"]
    elif not using_bvn and not user.nin_verified:
        user.nin_hash = attempt.identity_hash
        user.nin_last4 = attempt.identity_last4
        user.nin_verified = True
        fields += ["nin_hash", "nin_last4", "nin_verified"]
    if fields:
        user.recompute_tier()
        try:
            with db_transaction.atomic():
                user.save(update_fields=fields + ["tier"])
        except IntegrityError:
            attempt.status = WemaProvisioningAttempt.FAILED
            attempt.save(update_fields=["status", "updated"])
            return fail("This identity is already linked to another account. Contact support.",
                        status=409)
    attempt.status = WemaProvisioningAttempt.VERIFIED
    attempt.save(update_fields=["status", "updated"])
    # Read back the tier the BANK holds the NUBAN at. It runs its own ladder with its
    # own caps and enforces them regardless of ours, so knowing the real value lets us
    # refuse an over-limit transfer with a clear message instead of a failed payout.
    # Best-effort: a failure here must never block a successful provisioning.
    try:
        sync_bank_tier(wallet)
    except Exception:                                        # noqa: BLE001
        log.warning("wema_bank_tier_sync_failed user=%s", user.id, exc_info=True)
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
    if not tracking_id:
        return fail("Missing tracking reference")
    attempt = WemaProvisioningAttempt.objects.filter(
        user=user, tracking_id=tracking_id, status=WemaProvisioningAttempt.PENDING,
    ).first()
    if attempt is None or attempt.expired:
        return fail("This verification request has expired. Start account setup again.")
    using_bvn = attempt.identity_type == WemaProvisioningAttempt.BVN
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


@api
@ratelimit("simulate_deposit", limit=30, window=60)
def simulate_deposit(request):
    """POST /api/dev/simulate-deposit/ {token, phone, amount} -> {success, wallet}

    TEST-ONLY. Credits a mock inbound deposit — via the SAME path a real Wema NUBAN
    deposit takes — so the app can be walked fund -> transfer -> airtime end to end
    without real money or an approved SMS sender. Locked down three ways, ALL of
    which must hold:

      1. WEMA_SIMULATION must be on. A live-money deploy runs with simulation OFF,
         so this endpoint 404s there — it can NEVER fabricate real money.
      2. SIMULATE_DEPOSIT_TOKEN must be set and match (constant-time compare).
      3. The amount is bounded and the phone must belong to an existing user.

    Every use is logged loudly; `wema_preflight` HARD-FAILS while the token is set.
    Remove SIMULATE_DEPOSIT_TOKEN before go-live.
    """
    # Gate 1 — simulation only. 404 (not 403) so a live deploy gives no hint the
    # mechanism exists.
    if not wema_provider.wema_simulation():
        return fail("Not found", status=404)
    # Gate 2 — shared-secret token; fail closed when unset.
    configured = settings.SIMULATE_DEPOSIT_TOKEN
    supplied = (request.data.get("token") or "").strip()
    if not configured or not hmac.compare_digest(supplied, configured):
        return fail("Forbidden", status=403)
    # Gate 3 — target + bounded amount.
    phone = (request.data.get("phone") or "").strip()
    amount = parse_amount(request.data.get("amount"))
    if not phone:
        return fail("phone is required")
    if amount is None or amount < 100:
        return fail("Enter a valid amount (min ₦100)")
    if amount > 1_000_000:
        return fail("Simulated deposit is capped at ₦1,000,000 per call")
    user = User.objects.filter(phone=phone).first()
    if user is None:
        return fail("No user with that phone", status=404)

    # WEMA-CR- prefix + unique suffix => routed and idempotent exactly like a real
    # reconciled deposit (see apply_wema_credit / settle_reserved_funding).
    reference = f"WEMA-CR-SIM-{secrets.token_hex(6).upper()}"
    settle_reserved_funding(reference, amount, user)
    wallet = get_or_create_wallet(user)
    log.warning("simulate_deposit_used phone=%s amount=%s ref=%s — SIMULATE_DEPOSIT_TOKEN "
                "is set (WEMA_SIMULATION on); REMOVE before go-live",
                mask_pii(phone), amount, reference)
    return ok(success=True, wallet=str(wallet.balance), reference=reference,
              message="Simulated deposit credited")


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
