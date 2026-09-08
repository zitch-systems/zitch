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

from accounts.models import IdentityProof, hash_identifier, record_identity_proof
from common.http import (
    MIN_TRANSFER, api, check_daily_limit, check_send_limits, fail, idempotent_replay,
    mask_pii, ok, parse_amount, require_user, spend_key, verify_transaction_pin,
)
from common.ratelimit import ratelimit
from utility.providers import (funding_initialize, funding_verify, kyc_verify_face,
                               payment_provider)
from utility import wema as wema_provider

from .models import FundingIntent, Wallet, WemaProvisioningAttempt
from .services import (
    DuplicateTransaction,
    InsufficientFunds,
    LimitExceeded,
    attach_existing_bank_account,
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
        bvn_verified=user.bvn_verified,
        nin_verified=user.nin_verified,
        # The customer's registered legal name, so the Add-money screen can always
        # show whose account this is — even before it's provisioned, or on the rare
        # provider response that omits the holder name (account_name is blank).
        holder_name=(user.get_full_name() or "").strip(),
        **_account_setup_state(user, wallet),
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


def _active_wema_attempt(user, *, identity_type: str | None = None,
                         identity_hash: str | None = None):
    qs = WemaProvisioningAttempt.objects.filter(
        user=user,
        status=WemaProvisioningAttempt.PENDING,
        expires_at__gt=timezone.now(),
    )
    if identity_type:
        qs = qs.filter(identity_type=identity_type)
    if identity_hash:
        qs = qs.filter(identity_hash=identity_hash)
    return qs.order_by("-created").first()


def _account_setup_state(user, wallet) -> dict:
    if wallet.account_number:
        return {
            "account_setup_state": "ready",
            "otp_required": False,
            "identity_verified": bool(user.bvn_verified or user.nin_verified),
        }
    attempt = _active_wema_attempt(user)
    if attempt is not None:
        return {
            "account_setup_state": "otp_pending",
            "otp_required": True,
            "tracking_id": attempt.tracking_id,
            "using_bvn": attempt.identity_type == WemaProvisioningAttempt.BVN,
            # The attempt does not store or invent a phone destination. Wema\n            # owns the identity-linked line; expose only its identity kind.\n            "otp_destination": "",\n            "otp_destination_kind": "bvn" if attempt.identity_type == WemaProvisioningAttempt.BVN else "nin",
            "identity_verified": bool(user.bvn_verified or user.nin_verified),
        }
    if user.bvn_verified or user.nin_verified:
        return {
            "account_setup_state": "identity_verified",
            "otp_required": False,
            "identity_verified": True,
        }
    return {
        "account_setup_state": "identity_required",
        "otp_required": False,
        "identity_verified": False,
    }


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
    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())

    # A verified BVN is final. We retain only its keyed hash, so no later feature
    # may train customers to disclose the raw number again.
    if user.bvn_verified and bvn:
        return fail("Your BVN is already verified. You do not need to enter it again.", status=409)
    if user.bvn_verified and nin and not wallet.account_number:
        # A missing BVN NUBAN must not dead-end the customer. Try the provider
        # read-back first, but if Wema has not returned the account, continue
        # through the normal NIN onboarding path below. Wema supports NIN-based
        # account creation for existing customers; its OTP is bound to the NIN
        # record and does not require asking for the verified BVN again.
        recovered, _detail = attach_existing_bank_account(user, using_bvn=True)
        if recovered is not None and recovered.account_number:
            return ok(**_account_payload(
                recovered, already=True, tier=user.tier,
                bvn_verified=True, nin_verified=user.nin_verified,
                message="Your verified bank account has been reconnected."))

    if wallet.account_number:
        if len(bvn) == 11:
            payload, status = _verify_existing_wema_identity(
                user, wallet, WemaProvisioningAttempt.BVN, bvn)
            if payload.get("success"):
                return ok(**payload)
            return fail(payload.get("message", "Couldn't verify identity with Wema"), status=status)
        if len(nin) == 11:
            payload, status = _verify_existing_wema_identity(
                user, wallet, WemaProvisioningAttempt.NIN, nin)
            if payload.get("success"):
                return ok(**payload)
            return fail(payload.get("message", "Couldn't verify identity with Wema"), status=status)
        # already provisioned — return it (idempotent)
        return ok(**_account_payload(
            wallet, tier=user.tier, bvn_verified=user.bvn_verified, nin_verified=user.nin_verified))

    if len(bvn) != 11 and len(nin) != 11:
        if user.bvn_verified:
            recovered, _detail = attach_existing_bank_account(user, using_bvn=True)
            if recovered is not None and recovered.account_number:
                return ok(**_account_payload(
                    recovered, already=True, tier=user.tier,
                    bvn_verified=True, nin_verified=user.nin_verified,
                    message="Your verified bank account has been reconnected."))
            state = _account_setup_state(user, wallet)
            if state.get("account_setup_state") == "otp_pending":
                resend = wema_provider.resend_wallet_otp(
                    user.phone or "",
                    str(state.get("tracking_id") or ""),
                    bvn=bool(state.get("using_bvn")),
                )
                state["otp_resent"] = bool(resend.get("success"))
                message = (
                    "Your BVN is already verified. Wema sent the existing account "
                    "setup code again; enter it to finish issuing your account number."
                    if resend.get("success") else
                    "Your BVN is already verified. Enter the existing Wema account "
                    "setup code to finish issuing your account number."
                )
            else:
                message = (
                    "Your BVN is already verified. We are syncing your Wema account "
                    "number; you will not be asked to enter the BVN again."
                )
            return ok(
                **state,
                bvn_verified=True,
                nin_verified=user.nin_verified,
                holder_name=(user.get_full_name() or "").strip(),
                message=message,
            )
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
        # The bank already holds a customer record for this person, so there is
        # nothing left to create and every retry is refused for the same reason —
        # the account has to be READ BACK instead. Without this the funding screen
        # loops forever for anyone whose identity is verified but whose NUBAN never
        # reached us. Same recovery /api/wallet/wema/create/ already does.
        recovered = _adopt_existing_wema_account(user, using_bvn=using_bvn,
                                                 reason=res.get("message", ""))
        if recovered is not None:
            wallet = get_or_create_wallet(user)
            return ok(**_account_payload(
                wallet, already=True, tier=user.tier,
                bvn_verified=user.bvn_verified, nin_verified=user.nin_verified,
                message="Your bank account was already set up — we've reconnected it."))
        return fail(res.get("message", "Couldn't start account creation"), status=502)
    return ok(success=True, otp_required=True, tracking_id=res.get("tracking_id", ""),
              **_otp_delivery(res, using_bvn=using_bvn),
              using_bvn=using_bvn, mock=res.get("mock", False),
              message=_otp_prompt(using_bvn))


# ------------------- WEMA / ALAT wallet provisioning (OTP) -------------------
# Wema mints a dedicated NUBAN via a BVN/NIN + OTP round-trip and exposes NO
# inbound-credit webhook — deposits to the NUBAN are detected by the reconcile_wema
# poller. These three endpoints drive the OTP flow; they are gated on Wema being the
# funding rail (or configured).
def _wema_funding_enabled() -> bool:
    return (payment_provider() == "wema"
            or wema_provider.wema_live() or wema_provider.wema_simulation())


# --- Where the bank's code actually goes -----------------------------------
#
# ALAT validates the identity against its issuing register and then SMSes the
# consent code to the phone number ON THAT RECORD: the NIMC line for a NIN, the
# BVN line for a BVN. Not the number the customer registered with Zitch, and not
# the same line for both identities.
#
# Telling them otherwise is not a cosmetic slip. The customer stares at a handset
# that will never buzz, taps Resend (which re-sends to the same unreachable line),
# and concludes the NIN step is asking for a BVN code. The face route below is the
# real answer for an unreachable line — but only if the screen says so.
#
# So: describe the destination by IDENTITY, and quote a number only when ALAT
# itself returned one.
def _otp_prompt(using_bvn: bool) -> str:
    kind = "BVN" if using_bvn else "NIN"
    # "is sending", not "sent". What we actually observe is that Wema ACCEPTED the
    # request and handed back a tracking id; delivery happens on their side and we
    # get no confirmation of it either way. Stating it as done is a promise we
    # cannot keep — and it is currently being broken in production, where NIN
    # requests return 200 with a tracking id and no SMS reaches the customer (open
    # with Wema). Someone who then waits for a message that never comes reasonably
    # concludes the app is broken, so the sentence also names the route that does
    # work rather than leaving them to find it under "No code arriving?".
    return (f"Wema checked your {kind} and is sending a code by SMS to the phone number "
            "registered on it. Enter that code to finish.")


def _otp_delivery(res: dict | None, *, using_bvn: bool) -> dict:
    """The destination fields every OTP-issuing response returns.

    ``otp_destination`` stays empty unless the bank named a number, so a client
    rendering `otp_destination || "your phone"` can no longer be handed the wrong
    phone. ``otp_destination_kind`` is what clients should render from.
    """
    return {
        "otp_destination": str((res or {}).get("otp_destination") or ""),
        "otp_destination_kind": "bvn" if using_bvn else "nin",
    }


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
_ALREADY_ONBOARDED = re.compile(
    r"(already\s+exist|already\s+set\s*up|account\s+already|customer\s+already)",
    re.I,
)


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
    wallet, _detail = attach_existing_bank_account(user, using_bvn=using_bvn)
    if wallet is None or not wallet.account_number:
        return None
    return _account_payload(
        wallet, already=True,
        message="Your bank account was already set up — we've reconnected it.")


def _mark_identity_upgrade_required(wallet, required: bool = True) -> None:
    """Persist (or clear) "this NUBAN needs the combined upgrade".

    Written on the refusal rather than re-derived, because the only way to learn
    it is to ask the provider - and asking means having already collected the
    identity we are about to refuse.
    """
    if wallet is None or wallet.identity_upgrade_required == required:
        return
    wallet.identity_upgrade_required = required
    wallet.save(update_fields=["identity_upgrade_required"])


def _verify_existing_wema_identity(user, wallet, identity_type: str, raw_identity: str) -> tuple[dict, int]:
    kind = "bvn" if identity_type == WemaProvisioningAttempt.BVN else "nin"
    raw_identity = "".join(ch for ch in (raw_identity or "") if ch.isdigit())
    if len(raw_identity) != 11:
        return {"success": False, "message": f"Enter your 11-digit {kind.upper()}"}, 400
    if not wallet.account_number:
        return {"success": False, "message": "Set up your Wema account first"}, 400

    identity_hash = hash_identifier(raw_identity)
    if getattr(user, f"{kind}_verified", False) and getattr(user, f"{kind}_hash", "") == identity_hash:
        return _account_payload(
            wallet,
            already=True,
            upgraded=True,
            tier=user.tier,
            bvn_verified=user.bvn_verified,
            nin_verified=user.nin_verified,
            message=f"{kind.upper()} already verified with your existing Wema account",
        ), 200

    if _identity_owned_by_another_user(user, kind, raw_identity):
        return {
            "success": False,
            "message": f"This {kind.upper()} is already linked to another Zitch account",
        }, 409

    pending = WemaProvisioningAttempt.objects.filter(
        user=user,
        identity_type=identity_type,
        identity_hash=identity_hash,
        status=WemaProvisioningAttempt.PENDING,
        expires_at__gt=timezone.now(),
    ).order_by("-created").first()
    if pending is not None:
        return _account_payload(
            wallet,
            otp_required=True,
            tracking_id=pending.tracking_id,
            **_otp_delivery(None, using_bvn=identity_type == WemaProvisioningAttempt.BVN),
            using_bvn=identity_type == WemaProvisioningAttempt.BVN,
            tier=user.tier,
            bvn_verified=user.bvn_verified,
            nin_verified=user.nin_verified,
            message=_otp_prompt(identity_type == WemaProvisioningAttempt.BVN),
        ), 200

    # No OTP attempt here. Wema exposes no OTP continuation for a SECOND identity
    # on a NUBAN it has already created (see wema_wallet_upgrade_tier2) - the
    # combined existing-account upgrade is the only route. Asking anyway is what
    # produced the production defect this branch was rewritten for: the request
    # is made, the bank refuses with "customer already exists", and the customer
    # is told the number cannot be used only AFTER they have handed it over. On
    # WhatsApp that read as "enter your NIN securely" followed immediately by
    # "we can't take your NIN", in the same burst.
    #
    # Refusing up front is also what makes the state knowable without a provider
    # round-trip, so every surface can decline to ask in the first place.
    _mark_identity_upgrade_required(wallet)
    return {
        "success": False,
        "upgrade_required": True,
        "message": (
            "Your bank account is already open, so your bank needs the rest of your "
            "details together in one step - it can't take your "
            f"{kind.upper()} on its own.\n\n"
            "Open *Verify identity* in the Zitch app to finish. Nothing you've "
            "already verified is lost."
        ),
    }, 409



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
            # Wema sends this code to the phone held on the selected identity
            # record, not necessarily the Zitch account phone. Never invent a
            # destination when reusing an attempt.
            "otp_destination": "",
            "otp_destination_kind": identity_type,
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
    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    if user.bvn_verified and bvn:
        return fail("Your BVN is already verified. You do not need to enter it again.", status=409)
    if user.bvn_verified and nin and not wallet.account_number:
        # A missing BVN NUBAN must not block the independent NIN rail. Read back
        # an existing account first; if Wema has not returned one, continue to
        # _start_wema_attempt below so the NIN-linked SMS OTP can issue the account.
        recovered, _detail = attach_existing_bank_account(user, using_bvn=True)
        if recovered is not None and recovered.account_number:
            return ok(**_account_payload(
                recovered, already=True, tier=user.tier,
                bvn_verified=True, nin_verified=user.nin_verified,
                message="Your verified bank account has been reconnected."))
    if len(bvn) == 11:
        using_bvn = True
        identity_type = WemaProvisioningAttempt.BVN
        raw_identity = bvn
    elif len(nin) == 11:
        using_bvn = False
        identity_type = WemaProvisioningAttempt.NIN
        raw_identity = nin
    else:
        if wallet.account_number:
            return ok(**_account_payload(wallet, already=True,
                                         message="Your account is already set up"))
        return fail("Enter your 11-digit BVN or NIN")

    if wallet.account_number:
        payload, status = _verify_existing_wema_identity(user, wallet, identity_type, raw_identity)
        if payload.get("success"):
            return ok(**payload)
        # Forward upgrade_required. Without it every refusal looks alike to the
        # client, so it cannot tell "that number is wrong, try again" from "this
        # account can only be finished by the combined upgrade" - and it kept
        # offering the retry that can never succeed.
        extra = {"upgrade_required": True} if payload.get("upgrade_required") else {}
        return fail(payload.get("message", "Couldn't verify identity with Wema"),
                    status=status, **extra)
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
            wallet = get_or_create_wallet(user)
            return ok(**_account_payload(
                wallet,
                already=True,
                upgrade_required=True,
                message=(
                    "Your Wema account was reconnected. To verify another identity "
                    "on an existing Wema account, complete BVN, NIN and a live "
                    "selfie together."
                ),
            ))
        if _ALREADY_ONBOARDED.search(res.get("message", "") or ""):
            # Wema confirmed existing Wema customers can onboard, so this is not
            # a customer instruction to retry forever. It means Wallet Service
            # rejected one of the submitted creation fields as a duplicate and
            # support needs the provider-side reason/profile outcome.
            return fail("Your bank says these details are already registered. Contact Zitch support and we'll sort your account setup out.", status=409)
        return fail(res.get("message", "Couldn't start account creation"), status=502)
    # otp_required, like the account/create/ twin above. Both endpoints end the
    # same way - an attempt is open and the next call is verify-otp - so a client
    # that reads this flag to decide whether to show the code screen was getting
    # `undefined` from one of the two and skipping it.
    return ok(success=True, otp_required=True, tracking_id=res.get("tracking_id", ""),
              **_otp_delivery(res, using_bvn=using_bvn),
              using_bvn=using_bvn, mock=res.get("mock", False),
              # The bank's own success wording is dropped here on purpose. It is
              # free text from a Wallet Service that fronts both identity rails, so
              # it is not guaranteed to name the identity the customer actually
              # entered — and on this screen the identity IS the instruction, since
              # it is the only thing that tells them which handset to pick up. Our
              # wording is derived from the attempt, so it cannot disagree with it.
              # Failure paths still surface the gateway's message unchanged.
              message=_otp_prompt(using_bvn))


def complete_wema_provisioning(user, otp: str, tracking_id: str,
                               echoed_identity: str = "") -> tuple[dict, int]:
    """Validate a Wema account-creation OTP and finish provisioning: fetch and
    persist the NUBAN, lift the PND hold, and (name-match permitting) bind the
    verified identity. Returns (payload, http_status); payload["success"] says
    which. Shared by the app endpoint below and the WhatsApp add-account flow,
    so the AML-sensitive logic lives exactly once.

    The identity and its type come from the server-side initiation record, never
    from caller claims; echoed_identity, when supplied by older app clients, must
    match that record or the request is rejected loudly.
    """
    wallet = get_or_create_wallet(user)
    # NOTE: no early return when a NUBAN already exists. The bank's Account Creation
    # callback can provision it before the customer finishes the OTP step, and an
    # early return here would skip the KYC/tier block below — leaving them with a
    # working account permanently stuck at the tier-0 limit. Provisioning is skipped
    # when it is already done; identity verification still runs.
    already = bool(wallet.account_number)
    if not otp or not tracking_id:
        return {"success": False, "message": "Enter the OTP sent to your phone"}, 400
    attempt = WemaProvisioningAttempt.objects.filter(
        user=user, tracking_id=tracking_id, status=WemaProvisioningAttempt.PENDING,
    ).first()
    if attempt is None or attempt.expired:
        return {"success": False, "message": "This verification request has expired. Start account setup again."}, 400
    using_bvn = attempt.identity_type == WemaProvisioningAttempt.BVN
    # Older clients echo the raw value. It is not required, but if present it must
    # match the initiation record so tampering is rejected loudly rather than ignored.
    echoed = "".join(ch for ch in (echoed_identity or "") if ch.isdigit())
    if echoed and not hmac.compare_digest(hash_identifier(echoed), attempt.identity_hash):
        return {"success": False, "message": "Identity details do not match this verification request."}, 400
    val = wema_provider.validate_wallet_otp(user.phone or "", otp, tracking_id, bvn=using_bvn)
    if not val.get("success"):
        return {"success": False, "message": val.get("message", "OTP verification failed")}, 502
    if already:
        # Provisioned already (by an earlier verify, or by the bank's Account Creation
        # callback). Skip the provisioning write. The holder name is NOT read back from
        # the wallet here — it is resolved from the bank in the name-match block below,
        # for the reason spelled out there.
        holder_name = None
    else:
        acct = wema_provider.get_account_details(user.phone or "", bvn=using_bvn)
        if not acct.get("success") or not acct.get("account_number"):
            # ALAT can accept the OTP before its account-details endpoint is
            # populated. The OTP is one-time, so presenting its transient
            # "Account Details not found" response as a retry would make the
            # customer submit a spent credential. Keep the pending attempt for
            # the profiled Account Creation callback/reconciliation path.
            return {
                "success": False,
                "pending": True,
                "message": "Your identity was accepted. Your Zitch account is being created; we’ll message you when it is ready.",
            }, 202
        wallet, outcome = provision_wema_account(
            user, account_number=acct["account_number"],
            account_name=acct.get("account_name", ""), bank_name=acct.get("bank_name", ""),
            source="otp")
        if outcome.startswith("conflict"):
            log.warning("wema_account_conflict user=%s account=%s outcome=%s",
                        user.id, acct["account_number"], outcome)
            return {"success": False,
                    "message": "We couldn't finish setting up your account. Please contact support."}, 409
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
                record_identity_proof(
                    user,
                    IdentityProof.BVN if using_bvn else IdentityProof.NIN,
                    attempt.identity_hash,
                    source=IdentityProof.WEMA_WALLET_OTP,
                    provider_reference=attempt.tracking_id,
                    prehashed=True,
                )
        except IntegrityError:
            attempt.status = WemaProvisioningAttempt.FAILED
            attempt.save(update_fields=["status", "updated"])
            return {"success": False,
                    "message": "This identity is already linked to another account. Contact support."}, 409
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
    return {"success": True, **_account_payload(
        wallet, message="Your Zitch account is ready", tier=user.tier,
        bvn_verified=user.bvn_verified, nin_verified=user.nin_verified)}, 200


@api
@ratelimit("wema_wallet_upgrade_tier2", limit=5, window=60)
@require_user
def wema_wallet_upgrade_tier2(request):
    """POST /api/wallet/wema/upgrade-tier2/ {access_token,bvn,nin,live_image}

    Existing partnership accounts are upgraded by ALAT with one combined request:
    accountNumber + BVN + NIN + liveImageOfFace. The docs do not expose a separate
    OTP continuation for a second identity on an already-created NUBAN.
    """
    if not _wema_funding_enabled():
        return fail("Bank account upgrade is not available right now")
    user = request.user_obj
    wallet = get_or_create_wallet(user)
    if not wallet.account_number:
        return fail("Set up your Wema account first", status=400)
    bvn = "".join(ch for ch in (request.data.get("bvn") or "") if ch.isdigit())
    nin = "".join(ch for ch in (request.data.get("nin") or "") if ch.isdigit())
    live_image = (request.data.get("live_image") or request.data.get("selfie") or "").strip()
    if user.bvn_verified:
        if len(bvn) != 11:
            return fail(
                "Your bank needs your BVN, NIN and selfie in the same request, so "
                "please enter your BVN once more. It stays verified either way - "
                "we don't re-store it.",
                status=400,
            )
        if hash_identifier(bvn) != user.bvn_hash:
            return fail("That BVN does not match the BVN already verified on this account.", status=409)
    if len(bvn) != 11:
        return fail("Enter your 11-digit BVN")
    if len(nin) != 11:
        return fail("Enter your 11-digit NIN")
    if not live_image:
        return fail("Take a live selfie to complete the bank upgrade")
    if len(live_image) > 2_800_000:
        return fail("Selfie is too large. Retake it at a lower resolution.")
    if _identity_owned_by_another_user(user, WemaProvisioningAttempt.BVN, bvn):
        return fail("This BVN is already linked to another Zitch account", status=409)
    if _identity_owned_by_another_user(user, WemaProvisioningAttempt.NIN, nin):
        return fail("This NIN is already linked to another Zitch account", status=409)

    # Tier 2 is Wema's combined account-upgrade contract. The live image is
    # submitted only to Wema with the BVN and NIN; this route must not require a
    # separate verifier or turn a completed Wema upgrade into a local failure.
    res = wema_provider.upgrade_tier2(wallet.account_number, bvn=bvn, nin=nin,
                                     live_image=live_image)
    if not res.get("success"):
        return fail(res.get("message", "Wema could not upgrade this account right now"),
                    status=502)

    user.set_bvn(bvn)
    user.set_nin(nin)
    user.bvn_verified = True
    user.nin_verified = True
    user.face_verified = True
    user.recompute_tier()
    try:
        with db_transaction.atomic():
            user.save(update_fields=[
                "bvn_hash", "bvn_last4", "bvn_verified",
                "nin_hash", "nin_last4", "nin_verified",
                "face_verified", "tier",
            ])
            ref = str((res.get("raw") or {}).get("message") or "wema_tier2")[:128]
            record_identity_proof(user, IdentityProof.BVN, bvn,
                                  source=IdentityProof.WEMA_TIER2,
                                  provider_reference=ref)
            record_identity_proof(user, IdentityProof.NIN, nin,
                                  source=IdentityProof.WEMA_TIER2,
                                  provider_reference=ref)
    except IntegrityError:
        return fail("This identity is already linked to another account. Contact support.",
                    status=409)
    # The combined upgrade is exactly the step the flag was holding out for, so
    # clear it: the identity ladder is complete and nothing should route this
    # customer back here.
    _mark_identity_upgrade_required(wallet, False)
    try:
        sync_bank_tier(wallet)
    except Exception:  # noqa: BLE001
        log.warning("wema_bank_tier_sync_failed user=%s", user.id, exc_info=True)
    return ok(**_account_payload(wallet, upgraded=True, tier=user.tier,
                                 bvn_verified=user.bvn_verified,
                                 nin_verified=user.nin_verified,
                                 message="Bank identity upgrade complete"))



@api
@ratelimit("wema_wallet_verify", limit=10, window=60)
@require_user
def wema_wallet_verify_otp(request):
    """POST /api/wallet/wema/verify-otp/
       {access_token, otp, tracking_id, using_bvn?, bvn?, nin?}
    -> {success, account_number, account_name, bank_name, tier, bvn_verified, nin_verified}

    Step 2 of account setup — a thin HTTP shell over complete_wema_provisioning.
    """
    if not _wema_funding_enabled():
        return fail("Bank account creation is not available right now")
    payload, status = complete_wema_provisioning(
        request.user_obj,
        (request.data.get("otp") or "").strip(),
        (request.data.get("tracking_id") or "").strip(),
        echoed_identity=(request.data.get("bvn") or request.data.get("nin") or ""),
    )
    if payload.get("success"):
        return ok(**payload)
    return fail(payload.get("message", "OTP verification failed"), status=status)


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
    # A resend cannot move the code to a different handset — it goes back to the
    # same registered line. Say so, so the customer stops retrying a rail that
    # cannot reach them and takes the face route instead.
    return ok(success=True, **_otp_delivery(res, using_bvn=using_bvn),
              message=_otp_prompt(using_bvn))


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
        all_site_transactions=[_txn_row(t) for t in txns],
    )


def _txn_row(t) -> dict:
    """One transaction, in the shape the app's history list already expects.

    Extracted so the single-transaction lookup below cannot drift from the list:
    two hand-written copies of the same row is how a detail screen ends up
    disagreeing with the list it was opened from.
    """
    return {
        "service": t.service,
        "amount": str(t.amount),
        "transaction_status": t.transaction_status,
        "date": t.created.strftime("%Y-%m-%d %H:%M"),
        "reference": t.reference,
        "direction": t.direction,
        # The customer's own note. `note` is what a bank transfer stores
        # (execute_payout) and `narration` what a bill or top-up stores
        # (run_provider_purchase) — two keys for one idea, named differently long
        # before there was a field to fill either, and wallet.alerts._narration_line
        # already reads both the same way.
        "narration": str((t.meta or {}).get("note")
                         or (t.meta or {}).get("narration") or "")[:120],
    }


@api
@ratelimit("transaction_status", limit=120, window=60)
@require_user
def transaction_status(request):
    """POST /api/transaction/status/ {access_token, reference}
    -> {success, transaction: {...}}

    One transaction, read live. The detail screen was built entirely from the
    route params handed to it when the row was tapped, so a PENDING transfer
    displayed there stayed "Pending" forever — the reconciler settles the row
    minutes later, but nothing on that screen ever asked again. Customers
    reasonably read a permanently-pending transfer as lost money, which is the
    single worst thing a payments screen can imply.

    Scoped to the caller's OWN transactions: `user.transactions` is the filter,
    so a reference belonging to somebody else is a 404 here rather than a lookup
    oracle for other people's payment references.
    """
    user = request.user_obj
    reference = str(request.data.get("reference") or "").strip()
    if not reference:
        return fail("Missing transaction reference")
    txn = user.transactions.filter(reference=reference).first()
    if txn is None:
        return fail("Transaction not found", status=404)
    return ok(success=True, transaction=_txn_row(txn))


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


def apply_simulated_deposit(user, amount):
    """Credit bounded fake money to one already-authenticated test user.

    Shared by the token-protected HTTP endpoint and the WhatsApp simulation
    command. Authentication stays with the caller; this helper guarantees that
    the real-money rail is off and that the amount remains inside the test cap.
    """
    if not wema_provider.wema_simulation():
        raise ValueError("Simulation is disabled")
    amount = parse_amount(amount)
    if amount is None or amount < 100:
        raise ValueError("Enter a valid amount (min ₦100)")
    if amount > 1_000_000:
        raise ValueError("Simulated deposit is capped at ₦1,000,000 per call")

    # WEMA-CR- prefix + unique suffix => routed and idempotent exactly like a real
    # reconciled deposit (see apply_wema_credit / settle_reserved_funding).
    reference = f"WEMA-CR-SIM-{secrets.token_hex(6).upper()}"
    settle_reserved_funding(reference, amount, user)
    wallet = get_or_create_wallet(user)
    log.warning("simulate_deposit_used phone=%s amount=%s ref=%s — simulation is enabled; "
                "disable WEMA_SIMULATION before go-live",
                mask_pii(user.phone or ""), amount, reference)
    return wallet, reference


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

    wallet, reference = apply_simulated_deposit(user, amount)
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


@api
@ratelimit("statement_request", limit=6, window=3600)
@require_user
def statement_request(request):
    """POST /api/wallet/statement/request/
    {access_token, from, to, file_type: pdf|excel, include_address?, email?}
    -> {success, message}

    Renders the customer's own Zitch ledger for a date range and EMAILS it as a
    file. Emailed rather than returned inline on purpose: a statement is the one
    export people forward to a landlord, an embassy or an accountant, and a
    mailbox is where it needs to end up anyway. It also keeps a potentially large
    render off the response path.

    The address is included only when the customer asked for it — it is on the
    document for visa and loan applications that require it, and printing a home
    address on every statement someone forwards is a privacy leak, not a feature.
    """
    import re
    from datetime import datetime, timedelta

    from django.utils import timezone

    from utility.providers import send_email

    from .statement import build_statement_xlsx

    user = request.user_obj
    wallet = get_or_create_wallet(user)

    file_type = str(request.data.get("file_type") or "pdf").lower()
    if file_type not in ("pdf", "excel"):
        return fail("Choose either a PDF or an Excel file", status=400)

    def _day(v, default):
        v = str(v or "").strip()
        return v if re.match(r"^\d{4}-\d{2}-\d{2}$", v) else default

    today = timezone.localdate()
    date_to = _day(request.data.get("to"), today.strftime("%Y-%m-%d"))
    date_from = _day(request.data.get("from"), (today - timedelta(days=30)).strftime("%Y-%m-%d"))
    if date_from > date_to:
        return fail("The end date can't come before the start date", status=400)

    # The customer may send the statement somewhere other than their sign-in
    # address (an accountant, a landlord's agent), so an explicit `email` wins —
    # but it is only ever a destination, never a change to the account.
    email = str(request.data.get("email") or user.email or "").strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$", email):
        return fail("Add a valid email address to send the statement to", status=400)

    # Inclusive of the end DAY, not the end instant: "to 14 Aug" that silently
    # dropped everything after midnight on the 14th would look like missing money.
    start = timezone.make_aware(datetime.strptime(date_from, "%Y-%m-%d"))
    end = timezone.make_aware(datetime.strptime(date_to, "%Y-%m-%d")) + timedelta(days=1)
    txns = list(user.transactions.filter(created__gte=start, created__lt=end).order_by("-created")[:1000])

    from .models import Transaction

    def state(t) -> str:
        if t.transaction_status == Transaction.SUCCESS:
            return "success"
        if t.transaction_status == Transaction.FAILED:
            return "failed"
        return "pending"

    rows = []
    for t in txns:
        credit = t.direction == Transaction.IN
        rows.append({
            "date": timezone.localtime(t.created).strftime("%d %b %Y, %I:%M %p"),
            "label": (t.service or "").strip() or ("Credit" if credit else "Debit"),
            "amount": f"₦{t.amount:,.2f}",
            "signed_amount": f"{'' if credit else '-'}{t.amount}",
            "direction": "in" if credit else "out",
            "sign": "＋" if credit else "－",
            "status": state(t),
            "reference": t.reference or "",
        })

    period = f"{date_from} to {date_to}"
    if file_type == "excel":
        data = build_statement_xlsx(rows)
        filename = f"Zitch-Statement-{date_from}_{date_to}.xlsx"
    else:
        from whatsapp.receipt import render_statement_pdf

        address = ""
        if str(request.data.get("include_address") or "").lower() in ("1", "true", "yes"):
            address = " ".join(x for x in (
                getattr(user, "address", "") or "", getattr(user, "city", "") or "",
                getattr(user, "state", "") or "") if x).strip()
        data = render_statement_pdf(
            rows,
            balance=f"₦{wallet.balance:,.2f}",
            generated=timezone.localtime().strftime("%d %b %Y, %I:%M %p"),
            heading=f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}",
            holder=(user.get_full_name() or "").strip() or user.email,
            account=wallet.account_number or "",
            period=period,
            address=address,
        )
        filename = f"Zitch-Statement-{date_from}_{date_to}.pdf"

    sent = send_email(
        email,
        f"Your Zitch account statement ({period})",
        f"Your Zitch account statement for {period} is attached.\n\n"
        f"{len(rows)} transaction{'s' if len(rows) != 1 else ''}. "
        "If you didn't request this, please contact support@zitch.ng immediately.",
        attachments=[{"filename": filename, "content": data}],
    )
    if not sent.get("success"):
        return fail("We couldn't email your statement just now. Please try again.", status=502)
    return ok(success=True, message=f"Statement sent to {email}",
              from_date=date_from, to_date=date_to, count=len(rows))


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
    if amount < MIN_TRANSFER:
        return fail(f"Minimum transfer is ₦{MIN_TRANSFER:,.0f}")

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
        debit_txn, _ = transfer(sender, recipient, amount, note=data.get("note", ""),
                                idempotency_key=key, channel="app")
    except DuplicateTransaction:
        return idempotent_replay(existing_for_key(sender, key)) or fail("Duplicate request", status=409)
    except InsufficientFunds:
        return fail("Insufficient wallet balance", status=402)
    except LimitExceeded as exc:
        return fail(str(exc), status=403, code="limit_exceeded")

    wallet = get_or_create_wallet(sender)
    return ok(success=True, wallet=str(wallet.balance), reference=debit_txn.reference,
              narration=(debit_txn.meta or {}).get("narration", ""), message="Money sent")
