"""Single-use channel links bound to the credentials which authorised them."""
import re
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac

from common.phones import normalize_nigerian_mobile

from .auth_proof import credentials
from .models import PendingAction, WhatsAppLink

LINK_CODE_TTL = timedelta(minutes=30)


def _signed_code(user, nonce):
    signature = salted_hmac(
        "whatsapp.channel-link.v1", f"{nonce}:{credentials(user)}",
        algorithm="sha256",
    ).hexdigest()[:32]
    return (nonce + signature).upper()


def issue_link(user, credential_proof):
    """Recheck the PIN's credential snapshot before issuing its capability."""
    with transaction.atomic():
        current = get_user_model().objects.select_for_update().get(pk=user.pk)
        if (not current.is_active or current.pin_locked or current.pin_reset_required
                or not secrets.compare_digest(credential_proof, credentials(current))):
            return None
        WhatsAppLink.objects.filter(user=current, status=WhatsAppLink.PENDING).delete()
        return WhatsAppLink.objects.create(
            user=current, status=WhatsAppLink.PENDING,
            link_code=_signed_code(current, secrets.token_hex(16).upper()),
            expires_at=timezone.now() + LINK_CODE_TTL,
        )


def _lock_actions(user_id):
    # Same action -> user -> link order as login_flow and payment dispatch.
    # Browser grants revoke themselves against their deleted/replaced link.
    return list(PendingAction.objects.select_for_update().filter(user_id=user_id)
                .exclude(state="executing").exclude(action_type="verification_web"))


def redeem_link(code, msisdn):
    """Return (outcome, user); consumption and channel replacement are atomic."""
    if not code:
        return "missing", None
    candidate = WhatsAppLink.objects.filter(
        status=WhatsAppLink.PENDING, link_code=code).first()
    if candidate is None:
        return "missing", None
    with transaction.atomic():
        actions = _lock_actions(candidate.user_id)
        user = get_user_model().objects.select_for_update().get(pk=candidate.user_id)
        link = WhatsAppLink.objects.select_for_update().filter(
            pk=candidate.pk, status=WhatsAppLink.PENDING, link_code=code).first()
        if link is None:
            return "revoked", None
        # Burn every attempted capability, including unsigned pre-release codes.
        # Credentials are checked on the current locked account, never the
        # snapshot loaded with the original incoming WhatsApp message.
        valid = (re.fullmatch(r"[A-F0-9]{64}", code) is not None
                 and link.expires_at is not None and link.expires_at > timezone.now()
                 and user.is_active and not user.pin_locked and not user.pin_reset_required
                 and secrets.compare_digest(code, _signed_code(user, code[:32])))
        link.link_code = ""
        link.save(update_fields=["link_code"])
        if not valid:
            return "revoked", None
        registered = normalize_nigerian_mobile(user.phone)
        sender = normalize_nigerian_mobile(msisdn)
        if not registered or registered != sender:
            return "wrong_number", None
        if WhatsAppLink.objects.filter(wa_msisdn=msisdn, status=WhatsAppLink.ACTIVE).exclude(user=user).exists():
            return "revoked", None
        prior = list(WhatsAppLink.objects.select_for_update().filter(
            user=user, status=WhatsAppLink.ACTIVE).exclude(pk=link.pk))
        numbers = {old.wa_msisdn for old in prior} | {msisdn}
        PendingAction.objects.filter(pk__in=[pa.pk for pa in actions if pa.msisdn in numbers]).update(
            expires_at=timezone.now())
        if prior:
            link.ai_enabled = all(old.ai_enabled for old in prior)
            link.marketing_opt_in = all(old.marketing_opt_in for old in prior)
        WhatsAppLink.objects.filter(user=user).exclude(pk=link.pk).delete()
        link.wa_msisdn = msisdn
        link.status = WhatsAppLink.ACTIVE
        link.linked_at = timezone.now()
        link.save(update_fields=["wa_msisdn", "status", "linked_at", "ai_enabled", "marketing_opt_in"])
        # A channel replacement must not inherit an old number's read session.
        from .router import _mark_verified
        _mark_verified(msisdn)
        return "linked", user


def unlink(user):
    """Revoke active channels and outstanding invitations in one transaction."""
    with transaction.atomic():
        actions = _lock_actions(user.pk)
        get_user_model().objects.select_for_update().get(pk=user.pk)
        links = list(WhatsAppLink.objects.select_for_update().filter(user_id=user.pk))
        numbers = {link.wa_msisdn for link in links if link.wa_msisdn}
        PendingAction.objects.filter(pk__in=[pa.pk for pa in actions if pa.msisdn in numbers]).update(
            expires_at=timezone.now())
        WhatsAppLink.objects.filter(user_id=user.pk).delete()
