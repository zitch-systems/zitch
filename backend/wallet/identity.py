"""Finish a bank-accepted OTP after its account arrives, without replaying it."""
import hmac
import logging

from django.db import IntegrityError, transaction

from accounts.models import IdentityProof, User, record_identity_proof
from utility import wema

from .models import Wallet, WemaProvisioningAttempt

log = logging.getLogger("wallet")


def accepted_identity_pending(user):
    """Whether a consumed OTP awaits the final account/name check."""
    kinds = [kind for kind in ("bvn", "nin") if not getattr(user, f"{kind}_verified")]
    return WemaProvisioningAttempt.objects.filter(
        user=user, status=WemaProvisioningAttempt.PENDING,
        identity_type__in=kinds, otp_verified_at__isnull=False).exists()


def finish_accepted_identity(attempt, *, holder_name=None):
    """Return verified/pending/review/conflict/ignored from durable bank evidence.

    Require saved OTP acceptance and the bank's holder-name match. Account
    existence alone cannot verify identity. Network reads precede row locks;
    recheck the account and attempt under the lock before saving proof.
    """
    attempt.refresh_from_db()
    if attempt.status == WemaProvisioningAttempt.VERIFIED:
        return "verified"
    if (attempt.status != WemaProvisioningAttempt.PENDING
            or attempt.otp_verified_at is None
            or not (attempt.created <= attempt.otp_verified_at < attempt.expires_at)):
        return "ignored"
    wallet = Wallet.objects.filter(user_id=attempt.user_id).first()
    if not wallet or not wallet.account_number:
        return "pending"
    if not wema.wema_live() and wema._mock_blocked():
        return "pending"
    account_number = wallet.account_number
    if wema.wema_live():
        if holder_name is None:
            status = wema.get_kyc_status(account_number)
            if not status.get("success") or status.get("mock"):
                return "pending"
            holder_name = str(status.get("name") or "")
        # Callback nubanName and our substituted wallet name are not evidence.
        if not holder_name:
            return "pending"

    with transaction.atomic():
        # Match the wallet/user lock order used by money debits.
        current_wallet = Wallet.objects.select_for_update().get(user_id=attempt.user_id)
        user = User.objects.select_for_update().get(pk=attempt.user_id)
        locked = WemaProvisioningAttempt.objects.select_for_update().get(pk=attempt.pk)
        if locked.status == WemaProvisioningAttempt.VERIFIED:
            return "verified"
        if (locked.status != WemaProvisioningAttempt.PENDING
                or locked.otp_verified_at is None
                or not (locked.created <= locked.otp_verified_at < locked.expires_at)):
            return "ignored"
        if current_wallet.account_number != account_number:
            return "pending"
        if wema.wema_live() and wema.holder_name_mismatch(user.get_full_name(), holder_name):
            locked.status = WemaProvisioningAttempt.FAILED
            locked.save(update_fields=["status", "updated"])
            log.warning("wema_accepted_identity_name_mismatch user=%s attempt=%s", user.pk, locked.pk)
            return "review"
        kind = locked.identity_type
        flag, hash_field, last4_field = f"{kind}_verified", f"{kind}_hash", f"{kind}_last4"
        stored_hash = getattr(user, hash_field) or ""
        if ((stored_hash and not hmac.compare_digest(stored_hash, locked.identity_hash))
                or (getattr(user, flag) and not stored_hash)
                or User.objects.exclude(pk=user.pk).filter(**{hash_field: locked.identity_hash}).exists()):
            locked.status = WemaProvisioningAttempt.FAILED
            locked.save(update_fields=["status", "updated"])
            return "conflict"
        try:
            with transaction.atomic():
                setattr(user, hash_field, locked.identity_hash)
                setattr(user, last4_field, locked.identity_last4)
                setattr(user, flag, True)
                user.recompute_tier()
                user.save(update_fields=[hash_field, last4_field, flag, "tier"])
                proof = record_identity_proof(user, kind, locked.identity_hash,
                                              source=IdentityProof.WEMA_WALLET_OTP,
                                              provider_reference=locked.tracking_id, prehashed=True)
                proof.identity_last4 = locked.identity_last4
                proof.save(update_fields=["identity_last4"])
                locked.status = WemaProvisioningAttempt.VERIFIED
                locked.save(update_fields=["status", "updated"])
        except IntegrityError:
            locked.status = WemaProvisioningAttempt.FAILED
            locked.save(update_fields=["status", "updated"])
            return "conflict"
    log.info("wema_accepted_identity_completed user=%s attempt=%s kind=%s", user.pk, locked.pk, kind)
    return "verified"


def finish_pending_identities(user):
    """Complete only attempts whose bank OTP acceptance was recorded."""
    outcomes = []
    for attempt in WemaProvisioningAttempt.objects.filter(
            user=user, status=WemaProvisioningAttempt.PENDING,
            otp_verified_at__isnull=False).order_by("-created")[:2]:
        outcomes.append(finish_accepted_identity(attempt))
    return outcomes
