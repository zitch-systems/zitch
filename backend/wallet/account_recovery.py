"""Audited, single-session recovery of stale, unresolved face account issuance.

No bank creation is replayed here. The customer still completes the bank's face
check, and normal authenticated callbacks remain responsible for issuance.
"""
import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from utility import wema
from utility.providers import partnership_new_business_allowed
from whatsapp.models import AuditLog

from .models import Wallet, WemaFaceSession, WemaProvisioningAttempt


def active_account_recovery(user, *, identity_type=None, identity_hash=None):
    """Return only the latest, unused, identity-bound operator authorization."""
    if not user.is_active or Wallet.objects.filter(user=user).exclude(account_number="").exists():
        return None
    if WemaProvisioningAttempt.objects.filter(user=user, status="pending").filter(
            Q(otp_verified_at__isnull=False) | Q(expires_at__gt=timezone.now())).exists():
        return None
    session = WemaFaceSession.objects.filter(user=user).order_by("-created", "-pk").first()
    if (session is None or not session.is_account_recovery
            or session.status != WemaFaceSession.PENDING or session.expired
            or session.account_state != "unknown"
            or not getattr(user, f"{session.identity_type}_verified", False)
            or getattr(user, f"{session.identity_type}_hash", "") != session.identity_hash
            or identity_type is not None and session.identity_type != identity_type
            or identity_hash is not None and session.identity_hash != identity_hash):
        return None
    return session


def _eligible(user, kind):
    if not user.is_active or not partnership_new_business_allowed(user):
        raise ValueError("Account is not eligible for Partnership setup.")
    if not user.email_verified or not user.phone_verified:
        raise ValueError("Verified contact details are required.")
    identity_hash = getattr(user, f"{kind}_hash", "")
    if not getattr(user, f"{kind}_verified", False) or not identity_hash:
        raise ValueError("The selected identity must already be verified.")
    if User.objects.exclude(pk=user.pk).filter(**{f"{kind}_hash": identity_hash}).exists():
        raise ValueError("Identity ownership requires review.")
    if Wallet.objects.filter(user=user).exclude(account_number="").exists():
        raise ValueError("A funding account is already attached.")
    if WemaProvisioningAttempt.objects.filter(user=user, status="pending").filter(
            Q(otp_verified_at__isnull=False) | Q(expires_at__gt=timezone.now())).exists():
        raise ValueError("An OTP setup is active or already accepted.")
    sessions = WemaFaceSession.objects.filter(user=user)
    active = active_account_recovery(user, identity_type=kind, identity_hash=identity_hash)
    if active:
        return active
    if sessions.filter(status="pending", expires_at__gt=timezone.now()).exists():
        raise ValueError("A face setup is already active.")
    # Never convert a known acceptance/duplicate into a new account request.
    if sessions.filter(account_state="awaiting_callback").exists() or sessions.filter(
            account_failure_category="existing_customer").exists():
        raise ValueError("Bank acceptance or an existing customer requires bank reconciliation.")
    latest = sessions.order_by("-created", "-pk").first()
    if (latest is None or latest.status != "verified" or latest.is_account_recovery
            or latest.identity_type != kind or latest.identity_hash != identity_hash
            or latest.account_state != "unknown" or latest.account_failure_category
            or latest.updated > timezone.now() - timedelta(days=1)):
        raise ValueError("Only stale legacy issuance with an unknown outcome can be reviewed here.")
    return latest


def prepare_account_recovery(user_id, *, kind, operator_reference, approve=False):
    """Read-only by default; approval creates one challenge and an audit entry."""
    if kind not in ("bvn", "nin") or not operator_reference.strip():
        raise ValueError("Identity type and an operator review reference are required.")
    user = User.objects.get(pk=user_id)
    previous = _eligible(user, kind)
    if previous.is_account_recovery:
        return previous, False
    if not wema.wema_live() or not wema.face_verify_live():
        raise ValueError("Live account lookup and face verification must be configured.")
    lookup = wema.get_account_details(user.phone, bvn=kind == "bvn")
    if (lookup.get("mock") or lookup.get("account_number")
            or lookup.get("account_lookup_state") != "not_found"):
        raise ValueError("Bank lookup did not explicitly confirm no account; reconcile before retrying.")
    if not approve:
        return previous, False
    # Snapshot guards against contact/identity/session changes during the bank read.
    snapshot = (user.phone, user.email, getattr(user, f"{kind}_hash"), previous.pk)
    with transaction.atomic():
        # The callback locks its face session before its user. Preserve that
        # order, and never hold these locks during the provider read above.
        list(WemaFaceSession.objects.select_for_update().filter(user=user))
        Wallet.objects.select_for_update().get(user_id=user_id)
        user = User.objects.select_for_update().get(pk=user_id)
        previous = _eligible(user, kind)
        if previous.is_account_recovery:
            return previous, False
        if snapshot != (user.phone, user.email, getattr(user, f"{kind}_hash"), previous.pk):
            raise ValueError("Account setup changed during review; inspect it again.")
        session = WemaFaceSession.objects.create(
            user=user, state=secrets.token_urlsafe(32), identity_type=kind,
            identity_hash=getattr(user, f"{kind}_hash"), is_account_recovery=True,
            expires_at=timezone.now() + timedelta(hours=24))
        AuditLog.objects.create(
            actor_type="operator", actor_id="management-command",
            action="account_recovery_authorized", target=f"user:{user_id}",
            before={"session_id": previous.pk, "account_state": previous.account_state},
            after={"session_id": session.pk, "identity_type": kind,
                   "bank_lookup": "not_found", "http_status": lookup.get("http_status"),
                   "operator_reference": operator_reference[:120],
                   "expires_at": session.expires_at.isoformat()})
        return session, True


def start_recovery_session(session, *, minutes=20):
    """Narrow the authorization window when the customer requests their link.

    Run outside wallet/user locks (callbacks lock the session first). The
    conditional update cannot revive a consumed session or extend its deadline.
    """
    deadline = timezone.now() + timedelta(minutes=minutes)
    WemaFaceSession.objects.filter(pk=session.pk, status="pending", expires_at__gt=deadline).update(
        expires_at=deadline, updated=timezone.now())
    session.refresh_from_db()
    return session
