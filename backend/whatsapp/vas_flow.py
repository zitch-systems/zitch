"""Private virtual-account consent and identity entry inside WhatsApp.

Only a signed, linked customer session can reach enrollment. Identity digits are
never stored in plaintext. A short-lived encrypted cache capsule lets the same
authorized session finish after its provider-record ownership challenge, without
asking the customer to enter the identifier again.
"""
import hmac
import hashlib
import json
import logging
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_variables

from accounts.models import User, hash_identifier
from common.ratelimit import opaque_cache_identifier
from wallet.models import Wallet
from wema_vas.config import config
from wema_vas.enrollment import (
    consent_version, customer_enrollment_available, enroll_customer, named_identity_proof,
)
from .models import PendingAction, WhatsAppLink
from . import vas_capsule
from .vas_identity import identity_deadline, identity_timeout, lookup_failure

PREFIX = "va"
STATE = "flow_vas"
SETUP = "VAS_SETUP"
IDENTITY = "VAS_IDENTITY"
CODE = "VAS_CODE"
CODE_RETRY = "VAS_CODE_RETRY"
REENTRY = "VAS_REENTRY"
SCREENS = {SETUP, IDENTITY, CODE, CODE_RETRY, REENTRY, "RESULT"}
TTL = timedelta(minutes=15)
UNAVAILABLE = "Secure account setup is temporarily unavailable. Please try again here shortly."
PRIVATE_ENTRY = "Use the private verification form above. Do not send your BVN, NIN or code in this chat. Reply cancel to stop."
log = logging.getLogger("zitch.security")


def private_entry_active(msisdn):
    from django.db.models import Q
    return PendingAction.objects.filter(msisdn=msisdn, expires_at__gt=timezone.now()).filter(
        Q(action_type="vas_enroll", state=STATE)
        | Q(action_type="kyc", state="flow_identity", payload__vas_contacts=True)
        | Q(action_type="kyc", state="flow_identity", payload__vas_identity=True)
    ).exists()


def _configured():
    from .providers import flows_live
    cfg = getattr(settings, "WHATSAPP_FLOW", {}) or {}
    approved = cfg.get("VAS_APPROVED_FLOW_ID")
    return bool(cfg.get("VAS_ENROLLMENT_ENABLED") is True and approved
                and approved == cfg.get("FLOW_ID") and flows_live())


@lru_cache(maxsize=1)
def _contract_digest():
    document = json.loads((Path(__file__).parent / "flow_assets" / "pin_flow.json").read_text())
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ready():
    from .providers import published_flow_report
    if not _configured():
        return False
    approved = settings.WHATSAPP_FLOW["VAS_APPROVED_FLOW_ID"]
    report = published_flow_report()
    return bool(report.get("status") == "published" and report.get("flow_id") == approved
                and report.get("contract_matches") is True and not report.get("validation_errors")
                and report.get("published_contract_sha256") == _contract_digest()
                and SCREENS.issubset(set(report.get("published_screens") or [])))


def _credentials(user):
    return salted_hmac("whatsapp.vas.credentials.v1", ":".join(map(str, (
        user.pk, user.phone, user.email, user.email_verified, user.password, user.transaction_pin,
    ))), algorithm="sha256").hexdigest()


def _token(pa):
    from .flows import _sig
    return f"{PREFIX}{pa.pk}." + _sig(
        f"vas:{pa.pk}:{pa.user_id}:{pa.msisdn}:{pa.payload.get('link_id')}:"
        f"{pa.payload.get('credentials')}:{pa.payload.get('nonce')}:"
        f"{pa.payload.get('flow_id')}:{pa.payload.get('contract')}:"
        f"{pa.payload.get('mode')}:{pa.payload.get('consent_version')}"
    )


def _resolve(token):
    if not isinstance(token, str) or len(token) > 44:
        return None
    match = re.fullmatch(r"va([1-9][0-9]{0,18})\.([A-Za-z0-9_-]{22})", token)
    if match is None or int(match[1]) >= 2**63:
        return None
    pa = PendingAction.objects.filter(pk=int(match[1]), action_type="vas_enroll", state=STATE).first()
    if (pa is None or pa.expired or not isinstance(pa.payload, dict)
            or not pa.payload.get("nonce") or not pa.payload.get("credentials")):
        return None
    return pa if hmac.compare_digest(token, _token(pa)) else None


def _bound(pa, user):
    return bool(user.is_active and user.phone_verified and user.email and user.email_verified
                and pa.payload.get("mode") in {"validation", "live"}
                and pa.payload.get("mode") == config().get("MODE")
                and pa.payload.get("consent_version") == consent_version(pa.payload["mode"])
                and pa.payload.get("flow_id") == settings.WHATSAPP_FLOW.get("FLOW_ID")
                and pa.payload.get("contract") == _contract_digest()
                and hmac.compare_digest(_credentials(user), str(pa.payload.get("credentials") or ""))
                and WhatsAppLink.objects.filter(pk=pa.payload.get("link_id"), user=user,
                    wa_msisdn=pa.msisdn, status=WhatsAppLink.ACTIVE).exists())


@contextmanager
def _locked(token):
    """Use the same wallet -> user order as enrollment and bank callbacks."""
    pa = _resolve(token)
    if pa is None:
        yield None, None
        return
    with transaction.atomic():
        Wallet.objects.get_or_create(user_id=pa.user_id)
        Wallet.objects.select_for_update().get(user_id=pa.user_id)
        user = User.objects.select_for_update().get(pk=pa.user_id)
        pa = PendingAction.objects.select_for_update().filter(pk=pa.pk, state=STATE, action_type="vas_enroll").first()
        if (pa is None or pa.expired or not hmac.compare_digest(token, _token(pa))
                or not _bound(pa, user)):
            yield None, None
        else:
            yield pa, user


def _result(message, status="done"):
    return {"screen": "RESULT", "data": {"status": status, "message": message}}


def _finish(pa, message, status="done"):
    # Keep only a harmless terminal outcome and the binding, so a duplicate
    # exchange gives the same answer without reissuing proof or an account.
    vas_capsule.discard(pa)
    pa.payload = {key: pa.payload[key] for key in (
        "nonce", "credentials", "link_id", "flow_id", "contract", "mode", "consent_version")}
    pa.payload.update({"vas_step": "done", "message": message, "status": status})
    pa.save(update_fields=["payload"])
    return _result(message, status)


def _setup_data(mode):
    """Render the precise purpose approved by this signed setup session."""
    identity = (
        "Verify your BVN privately, or choose NIN. We send an ownership code by SMS to the phone "
        "on your identity record and also try its registered email when available. "
    )
    if mode == "validation":
        return {
            "title": "Set up your Zitch account",
            "purpose": (
                "Zitch verifies your BVN or NIN with Prembly, stores your verified identity "
                "and phone in encrypted form, and shares them with Wema Bank for bank integration "
                "validation. Funding becomes available after account activation."
            ),
            "consent_text": identity + (
                "By tapping I agree and continue, you consent to this verification, storage and "
                "sharing for bank integration validation. Close this form to decline."
            ),
            "error": "",
        }
    if mode == "live":
        return {
            "title": "Set up your Zitch account",
            "purpose": (
                "Zitch checks your BVN or NIN with Prembly, securely stores your verified identity "
                "and phone in encrypted form, and shares them with Wema Bank to operate your "
                "funding account. Your existing balance is unchanged."
            ),
            "consent_text": identity + (
                "By tapping I agree and continue, you consent to this verification, storage and "
                "sharing. Close this form to decline."
            ),
            "error": "",
        }
    raise ValueError("Unknown account setup purpose")


def _screen(pa):
    step = pa.payload.get("vas_step")
    if step == "consent":
        return {"screen": SETUP, "data": _setup_data(pa.payload["mode"])}
    if step == "done":
        return _result(pa.payload["message"], pa.payload["status"])
    kind = str(pa.payload.get("id_kind", "bvn")).upper()
    screen = pa.payload.get("screen", IDENTITY)
    if step == "code":
        delivery = pa.payload.get("id_otp_delivery") or {}
        target = delivery.get("delivery") or pa.payload.get("id_otp_to", "")
        channels = delivery.get("delivery_channels") or ["sms"]
        via = "SMS and email" if "email" in channels else "SMS"
        summary = f"Enter the code sent by {via} to your {kind} record contacts ({target})."
        if delivery.get("delivery_notice"):
            summary += " " + delivery["delivery_notice"]
        label = "Verification code"
    elif step == "reentry":
        # A form opened before one-entry setup has no recoverable input. Do not
        # send another re-entry screen; an already displayed legacy submission
        # may still use the guarded _identity path below.
        return _finish(pa, "This setup session needs to be restarted. Close this form and reply 6. "
                       "Your saved verification is unchanged.", "Not completed")
    elif step == "processing":
        summary, label = "Your identity is being checked. Please wait for your verification code.", kind
    else:
        summary, label = f"Enter your 11-digit {kind} privately to set up your account.", kind
    return {"screen": screen, "data": {"summary": summary, "label": label,
                                        "error": str(pa.payload.get("error") or "")}}


@sensitive_variables()
def _request_digest(screen, data):
    fields = {"consent": data.get("consent"), "identity_type": data.get("identity_type")} if screen == SETUP else {"number": data.get("number")}
    return salted_hmac("whatsapp.vas.accepted-request.v1",
        json.dumps([screen, fields], sort_keys=True, separators=(",", ":")), algorithm="sha256").hexdigest()


def _replay(pa, screen, digest):
    accepted = pa.payload.get("accepted_requests", {}).get(screen)
    if accepted is None:
        return None
    if not hmac.compare_digest(accepted, digest):
        return _finish(pa, "This setup submission changed. Start again in the chat.", "Not completed")
    return _screen(pa)


@sensitive_variables()
def start(user, msisdn):
    from .providers import send_flow
    from .router import EXECUTING_STATE, _clear_actions, _start_kyc, reply
    if not customer_enrollment_available(user) or not ready():
        return reply(msisdn, UNAVAILABLE)
    mode = config()["MODE"]
    setup_data = _setup_data(mode)
    link = WhatsAppLink.objects.filter(user=user, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()
    if not link or not user.is_active or not user.phone_verified:
        return reply(msisdn, "Please sign in securely on WhatsApp before setting up your account.")
    if not user.email or not user.email_verified:
        return _start_kyc(user, msisdn)
    if not cache.add("wa-vas-start:" + opaque_cache_identifier("wa-vas-start", str(user.pk)), True, 60):
        return reply(msisdn, "Please wait a minute before opening another setup form.")
    with transaction.atomic():
        pending = list(PendingAction.objects.select_for_update().filter(msisdn=msisdn))
        if any((item.state == EXECUTING_STATE and item.action_type != "unlock")
               or (item.action_type == "verification_web" and item.state in {"web_processing", "web_review"})
               for item in pending):
            return reply(msisdn, "Your earlier payment or verification is still being completed. Please wait for its outcome before starting account setup.")
        _clear_actions(msisdn)
        pa = PendingAction.objects.create(user=user, msisdn=msisdn, action_type="vas_enroll", state=STATE,
            payload={"vas_step": "consent", "nonce": secrets.token_urlsafe(16), "link_id": link.pk,
                     "credentials": _credentials(user), "flow_id": settings.WHATSAPP_FLOW["FLOW_ID"],
                     "contract": _contract_digest(), "mode": mode,
                     "consent_version": consent_version(mode)}, expires_at=timezone.now() + TTL)
    result = send_flow(msisdn, _token(pa), header="Set up your Zitch account",
        body="Verify your identity and set up your account privately here. Your existing profile and balance are unchanged.",
        screen=SETUP, screen_data=setup_data, cta="Set up securely", on_open="data_exchange")
    if not result.get("success") or result.get("mock"):
        pa.delete()
        return reply(msisdn, UNAVAILABLE)
    return result


def _has_proof(user, kind, digest):
    if not (getattr(user, f"{kind}_verified")
            and hmac.compare_digest(getattr(user, f"{kind}_hash") or "", digest)):
        return False
    return named_identity_proof(user, kind, digest) is not None


@sensitive_variables()
def _enroll(pa, user, kind, number):
    """Use the normal proof/consent allocator while holding the session locks."""
    if (pa.expired or pa.payload.get("consent") is not True
            or not _has_proof(user, kind, hash_identifier(number))):
        return _finish(pa, "This setup session needs to be restarted. Close this form and reply 6. "
                       "Your saved verification is unchanged.", "Not completed")
    try:
        account = enroll_customer(user, **{kind: number}, consent=True,
            expected_mode=pa.payload["mode"],
            expected_consent_version=pa.payload["consent_version"],
            consent_reference=f"{pa.payload['consent_version']}:whatsapp:{pa.pk}:{pa.payload['consent_at']}")
    except ValidationError:
        return _finish(pa, "Your account setup needs review. Contact Zitch Support here; your existing balance is unchanged.", "Not completed")
    if account.mode == "validation":
        return _finish(pa, "Your details are verified and your account setup is complete.",
                       "Account activation pending")
    from wallet.services import customer_funding_account
    from .router import _funding_spending_notice
    notice = _funding_spending_notice(customer_funding_account(user)).strip()
    return _finish(pa, "Your funding account is ready. Close this form and reply 6 to view it. " + notice, "Successful")


@sensitive_variables()
def _identity(token, data, screen, request_digest, *, deadline):
    number = data.get("number")
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{11}", number):
        with _locked(token) as (pa, _user):
            if pa is None:
                return _result("This setup has ended. Start again in the chat.")
            return _finish(pa, "Enter exactly 11 digits in a new private setup form.", "Not completed")
    from accounts.views import _identity_owned_by_another_user
    with _locked(token) as (pa, user):
        if pa is None:
            return _result("This setup has ended. Start again in the chat.")
        replay = _replay(pa, screen, request_digest)
        if replay is not None:
            return replay
        if pa.payload.get("vas_step") not in {"identity", "reentry"}:
            return _screen(pa) if pa.payload.get("vas_step") == "done" else _result("This step is already processing. Return to the chat.")
        if pa.payload.get("consent") is not True or not customer_enrollment_available(user):
            return _finish(pa, UNAVAILABLE, "Not completed")
        kind = pa.payload["id_kind"]
        digest = hash_identifier(number)
        # A mistyped second entry must never replace the identity proved by the
        # code. Explain the recovery without discarding its existing proof or
        # disclosing the entered identifier / another profile's ownership.
        if (pa.payload.get("vas_step") == "reentry"
                and not hmac.compare_digest(pa.payload.get("identity_hash", ""), digest)):
            log.warning("wa_vas_identity_rejected category=reentry_mismatch")
            if _has_proof(user, kind, pa.payload.get("identity_hash", "")):
                return _finish(pa, f"That {kind.upper()} does not match the one you just verified. "
                    f"Close this form and reply 6 to start again using the same {kind.upper()}. "
                    "Your successful identity verification is saved.", "Not completed")
            return _finish(pa, "Your verification needs review. Contact Zitch Support here.", "Not completed")
        if _identity_owned_by_another_user(user, kind, number):
            log.warning("wa_vas_identity_rejected category=identity_conflict")
            return _finish(pa, "Those details could not be confirmed. Start again with your own verified identity.", "Not completed")
        if (getattr(user, f"{kind}_verified")
                and not hmac.compare_digest(getattr(user, f"{kind}_hash") or "", digest)):
            log.warning("wa_vas_identity_rejected category=verified_identity_mismatch")
            return _finish(pa, "Those details do not match the identity already verified on this profile. "
                "Start again with that identity, or contact Zitch Support here.", "Not completed")
        if _has_proof(user, kind, digest):
            return _enroll(pa, user, kind, number)
        if pa.payload.get("vas_step") == "reentry":
            log.warning("wa_vas_identity_rejected category=reentry_proof_missing")
            return _finish(pa, "Your verification needs review. Contact Zitch Support here.", "Not completed")
        pa.payload.update({"vas_step": "processing", "identity_hash": digest,
            "identity_previous_hash": getattr(user, f"{kind}_hash"), "identity_last4": number[-4:]})
        try:
            vas_capsule.store(pa, number)
        except vas_capsule.CapsuleUnavailable:
            log.warning("wa_vas_identity_failed category=private_entry_unavailable")
            return _finish(pa, UNAVAILABLE, "Not completed")
        pa.payload.setdefault("accepted_requests", {})[screen] = request_digest
        pa.save(update_fields=["payload"])
    # Reuse the same authoritative lookup and registered-phone ownership
    # challenge as KYC. No account creation / Partnership fallback is allowed.
    from utility.providers import _prembly_identity_live, _record_email, prembly_verify_bvn, prembly_verify_nin
    from .router import _kyc_send_identity_otp
    # Prembly can legitimately need more than two seconds. All provider calls
    # share one exchange budget; delivery consumes only what lookup leaves.
    result = {}
    failure = "lookup_unconfigured"
    if _prembly_identity_live():
        budget = identity_timeout(deadline, 6, read=5.5)
        if budget is None:
            failure = "lookup_budget_exhausted"
        else:
            result = (prembly_verify_bvn if kind == "bvn" else prembly_verify_nin)(number,
                name=user.get_full_name() or "", timeout=budget)
            failure = lookup_failure(result)
    error = "lookup unavailable"
    if not failure:
        name = " ".join(result.get(key, "").strip() for key in ("first_name", "middle_name", "last_name")).strip()
        error = _kyc_send_identity_otp(pa, user, kind, result["phone"], verified_name=name,
            email=_record_email(result), delivery_deadline=deadline)
    else:
        log.warning("wa_vas_identity_failed category=%s", failure)
    with _locked(token) as (current, _user):
        if current is None:
            return _result("This setup has ended. Start again in the chat.")
        if error is None and current.payload.get("id_otp_hash"):
            current.payload.update({"vas_step": "code", "screen": CODE})
            current.save(update_fields=["payload"])
            return _screen(current)
        return _finish(current, "We could not complete identity verification. Please try again here later. No account was created.", "Not completed")


@sensitive_variables()
def handle(token, action, data, screen=""):
    deadline = identity_deadline()
    from .flows import _close_flow
    # Publication is proven before issuing this short-lived signed session.
    # Bind that immutable Flow ID and exact local contract to every exchange;
    # never spend Meta's data-exchange deadline on another Graph probe.
    if not _configured():
        return _close_flow(token)
    try:
        with _locked(token) as (pa, user):
            if pa is None:
                return _close_flow(token)
            if not customer_enrollment_available(user):
                return _finish(pa, UNAVAILABLE, "Not completed")
            step = pa.payload.get("vas_step")
            if step == "done":
                return _close_flow(token) if data.get("close") is True or action == "INIT" else _screen(pa)
            if action == "INIT":
                return _screen(pa) if step == "consent" else _close_flow(token)
            if action != "data_exchange" or data.get("close") is True:
                return _finish(pa, "Setup cancelled. No new account was created.")
            request_digest = _request_digest(screen, data)
            replay = _replay(pa, screen, request_digest)
            if replay is not None:
                return replay
            if screen != pa.payload.get("screen", SETUP):
                return _finish(pa, "This setup step changed. Start again in the chat.", "Not completed")
            if step == "consent":
                if data.get("consent") is not True or data.get("identity_type") not in {"bvn", "nin"}:
                    return _finish(pa, "Setup cancelled. Your identity was not submitted.")
                pa.payload.update({"consent": True, "consent_at": timezone.now().isoformat(),
                    "id_kind": data["identity_type"], "vas_step": "identity", "screen": IDENTITY})
                pa.payload.setdefault("accepted_requests", {})[screen] = request_digest
                pa.save(update_fields=["payload"])
                return _screen(pa)
            if step == "code":
                from .router import kyc_flow_identity_otp
                try:
                    number = vas_capsule.recover(pa)
                except vas_capsule.CapsuleUnavailable:
                    log.warning("wa_vas_identity_failed category=private_entry_unavailable")
                    return _finish(pa, "This setup session needs to be restarted. Close this form and reply 6. "
                                   "Your saved verification is unchanged.", "Not completed")
                outcome, _message = kyc_flow_identity_otp(pa, data.get("number", ""))
                pa.refresh_from_db()
                pa.payload.setdefault("accepted_requests", {})[screen] = request_digest
                if outcome == "ok":
                    user.refresh_from_db()
                    return _enroll(pa, user, pa.payload["id_kind"], number)
                elif outcome == "retry" and pa.payload.get("screen") == CODE:
                    pa.payload.update({"screen": CODE_RETRY, "error": "Check your verification code and try once more."})
                else:
                    return _finish(pa, "The code could not be confirmed. Start again in the chat.", "Not completed")
                pa.save(update_fields=["payload"])
                return _screen(pa)
            if step not in {"identity", "reentry"}:
                return _result("This setup is processing. Return to the chat.")
        return _identity(token, data, screen, request_digest, deadline=deadline)
    except Exception as exc:  # never put provider details or submitted IDs in logs/outcomes
        log.warning("wa_vas_setup_failed error_type=%s", type(exc).__name__)
        with _locked(token) as (pa, _user):
            if pa is not None:
                return _finish(pa, "We could not confirm the result. Reply 6 in the chat to check your account before trying again.", "Not completed")
        return _close_flow(token)
