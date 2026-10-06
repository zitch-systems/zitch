"""Private virtual-account consent and identity entry inside WhatsApp.

Only a signed, linked customer session can reach enrollment. Identity digits are
used in memory for lookup/enrollment and are never kept in PendingAction or a
cache. When new ownership proof is needed, the existing provider/SMS challenge
records it first; the customer then re-enters the identifier on a fresh screen.
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
from urllib3.util import Timeout

from accounts.models import IdentityProof, User, hash_identifier
from common.ratelimit import opaque_cache_identifier
from wallet.models import Wallet
from wema_vas.enrollment import CONSENT_VERSION, enrollment_available, enroll_verified
from .models import PendingAction, WhatsAppLink

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
PRIVATE_ENTRY = "Use the private setup form above. Do not send your BVN, NIN or code in this chat. Reply cancel to stop."
log = logging.getLogger("zitch.security")


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
        user.pk, user.phone, user.password, user.transaction_pin,
    ))), algorithm="sha256").hexdigest()


def _token(pa):
    from .flows import _sig
    return f"{PREFIX}{pa.pk}." + _sig(
        f"vas:{pa.pk}:{pa.user_id}:{pa.msisdn}:{pa.payload.get('link_id')}:"
        f"{pa.payload.get('credentials')}:{pa.payload.get('nonce')}:"
        f"{pa.payload.get('flow_id')}:{pa.payload.get('contract')}"
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
    return bool(user.is_active and user.phone_verified
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
    pa.payload = {key: pa.payload[key] for key in ("nonce", "credentials", "link_id", "flow_id", "contract")}
    pa.payload.update({"vas_step": "done", "message": message, "status": status})
    pa.save(update_fields=["payload"])
    return _result(message, status)


def _screen(pa):
    step = pa.payload.get("vas_step")
    if step == "consent":
        return {"screen": SETUP, "data": {"error": ""}}
    if step == "done":
        return _result(pa.payload["message"], pa.payload["status"])
    kind = str(pa.payload.get("id_kind", "bvn")).upper()
    screen = pa.payload.get("screen", IDENTITY)
    if step == "code":
        summary = f"Enter the SMS code sent to the phone on your {kind} record ({pa.payload.get('id_otp_to', '')})."
        label = "SMS code"
    elif step == "reentry":
        summary = f"Identity confirmed. Re-enter the same {kind} to finish. We did not retain your earlier entry."
        label = kind
    elif step == "processing":
        summary, label = "Your identity is being checked. Please wait for the SMS code.", kind
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
    from .router import EXECUTING_STATE, _clear_actions, reply
    if not enrollment_available(user) or not ready():
        return reply(msisdn, UNAVAILABLE)
    link = WhatsAppLink.objects.filter(user=user, wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).first()
    if not link or not user.is_active or not user.phone_verified:
        return reply(msisdn, "Please sign in securely on WhatsApp before setting up your account.")
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
                     "contract": _contract_digest()}, expires_at=timezone.now() + TTL)
    result = send_flow(msisdn, _token(pa), header="Set up your Zitch account",
        body="Set up your funding account privately here. Your identity details never appear in the chat.",
        screen=SETUP, screen_data={"error": ""}, cta="Set up securely", on_open="data_exchange")
    if not result.get("success") or result.get("mock"):
        pa.delete()
        return reply(msisdn, UNAVAILABLE)
    return result


def _has_proof(user, kind, digest):
    return bool(getattr(user, f"{kind}_verified")
        and hmac.compare_digest(getattr(user, f"{kind}_hash") or "", digest)
        and IdentityProof.objects.filter(user=user, identity_type=kind, identity_hash=digest,
            source__in=[source for source, _ in IdentityProof.SOURCE_CHOICES]).exclude(verified_name="").exists())


@sensitive_variables()
def _identity(token, data, screen, request_digest):
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
        if pa.payload.get("consent") is not True or not enrollment_available(user):
            return _finish(pa, UNAVAILABLE, "Not completed")
        kind = pa.payload["id_kind"]
        digest = hash_identifier(number)
        if (_identity_owned_by_another_user(user, kind, number)
                or (getattr(user, f"{kind}_verified") and not hmac.compare_digest(getattr(user, f"{kind}_hash") or "", digest))
                or (pa.payload.get("vas_step") == "reentry" and not hmac.compare_digest(pa.payload.get("identity_hash", ""), digest))):
            return _finish(pa, "Those details could not be confirmed. Start again with your own verified identity.", "Not completed")
        if _has_proof(user, kind, digest):
            try:
                enroll_verified(user, **{kind: number}, consent=True,
                    consent_reference=f"{CONSENT_VERSION}:whatsapp:{pa.pk}:{pa.payload['consent_at']}")
            except ValidationError:
                return _finish(pa, "Your account setup needs review. Contact Zitch Support here; your existing balance is unchanged.", "Not completed")
            from wallet.services import customer_funding_account
            from .router import _funding_spending_notice
            notice = _funding_spending_notice(customer_funding_account(user)).strip()
            return _finish(pa, "Your funding account is ready. Close this form and reply 6 to view it. " + notice, "Successful")
        if pa.payload.get("vas_step") == "reentry":
            return _finish(pa, "Your verification needs review. Contact Zitch Support here.", "Not completed")
        pa.payload.update({"vas_step": "processing", "identity_hash": digest,
            "identity_previous_hash": getattr(user, f"{kind}_hash"), "identity_last4": number[-4:]})
        pa.payload.setdefault("accepted_requests", {})[screen] = request_digest
        pa.save(update_fields=["payload"])
    # Reuse the same authoritative lookup and registered-phone ownership
    # challenge as KYC. No account creation / Partnership fallback is allowed.
    from utility.providers import _prembly_live, prembly_verify_bvn, prembly_verify_nin
    from .router import _kyc_send_identity_otp
    # Two sequential external calls share the encrypted exchange's short
    # response window. Keep separate fresh three-second budgets so a slow
    # identity provider or SMS service cannot inherit the ordinary 30s limit.
    result = (prembly_verify_bvn if kind == "bvn" else prembly_verify_nin)(number,
        name=user.get_full_name() or "", timeout=Timeout(total=3, connect=1, read=2)) if _prembly_live() else {}
    name = " ".join(str(result.get(key) or "").strip() for key in ("first_name", "middle_name", "last_name")).strip()
    error = "lookup unavailable"
    if result.get("success") is True and not result.get("mock") and name and result.get("phone"):
        error = _kyc_send_identity_otp(pa, user, kind, result["phone"], verified_name=name,
            timeout=Timeout(total=3, connect=1, read=2))
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
            if not enrollment_available(user):
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
                outcome, _message = kyc_flow_identity_otp(pa, data.get("number", ""))
                pa.refresh_from_db()
                pa.payload.setdefault("accepted_requests", {})[screen] = request_digest
                if outcome == "ok":
                    pa.payload.update({"vas_step": "reentry", "screen": REENTRY, "error": ""})
                elif outcome == "retry" and pa.payload.get("screen") == CODE:
                    pa.payload.update({"screen": CODE_RETRY, "error": "Check your SMS code and try once more."})
                else:
                    return _finish(pa, "The code could not be confirmed. Start again in the chat.", "Not completed")
                pa.save(update_fields=["payload"])
                return _screen(pa)
            if step not in {"identity", "reentry"}:
                return _result("This setup is processing. Return to the chat.")
        return _identity(token, data, screen, request_digest)
    except Exception as exc:  # never put provider details or submitted IDs in logs/outcomes
        log.warning("wa_vas_setup_failed error_type=%s", type(exc).__name__)
        with _locked(token) as (pa, _user):
            if pa is not None:
                return _finish(pa, "We could not confirm the result. Reply 6 in the chat to check your account before trying again.", "Not completed")
        return _close_flow(token)
