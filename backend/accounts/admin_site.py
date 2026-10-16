"""Django admin uses the same operator MFA policy as both portal login routes."""
import hmac
import json

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.crypto import salted_hmac
from django.utils.decorators import method_decorator

from common.http import mask_pii
from common.ratelimit import (
    clear_login_failures, login_locked, note_login_failure, ratelimit,
)

SESSION_PROOF_KEY = "zitch_admin_factor_proof"


def _factor_proof(user, factor):
    """An opaque session binding; never persist the seed or its unkeyed hash.

    Ciphertext and last_step change on ordinary logins. Bind to the underlying
    seed and enrolment instead so another valid login cannot revoke this session.
    """
    if factor is None:
        material = "unenrolled"
    else:
        secret = factor.plaintext_secret()
        if not secret:
            return ""
        material = f"{factor.pk}:{factor.confirmed_at}:{secret}"
    return salted_hmac(
        "zitch.django-admin.factor.v1",
        f"{user.pk}:{user.get_session_auth_hash()}:{material}",
        algorithm="sha256",
    ).hexdigest()


class ZitchAdminAuthenticationForm(AdminAuthenticationForm):
    code = forms.CharField(
        label="Authentication code", required=False, max_length=6,
        widget=forms.PasswordInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
        help_text="Enter the current code from your authenticator if you have enabled two-factor authentication.",
    )

    def clean(self):
        from admin_api.views import _mfa_login_error
        from accounts.models import OperatorTotp
        from whatsapp.ops import record_audit

        identifier = str(self.cleaned_data.get("username") or "").strip()
        if login_locked("django-admin", identifier):
            record_audit("django_admin.login_locked", target=mask_pii(identifier), actor_type="system")
            raise ValidationError("Too many failed attempts. Try again later.", code="locked")
        try:
            cleaned = super().clean()
        except ValidationError:
            note_login_failure("django-admin", identifier)
            record_audit("django_admin.login_failed", target=mask_pii(identifier), actor_type="system")
            raise
        user = self.get_user()
        if user is None or self.errors:
            return cleaned
        with transaction.atomic():
            factor = OperatorTotp.objects.select_for_update().filter(user=user, confirmed=True).first()
            # Bind to the factor that was checked, not a replacement enrolled
            # concurrently after validation. The shared helper locks/burns its code.
            proof = _factor_proof(user, factor)
            error = _mfa_login_error(user, cleaned.get("code") or "")
        if error is not None:
            note_login_failure("django-admin", identifier)
            record_audit("django_admin.login_mfa_failed", target=mask_pii(identifier), actor_type="system")
            body = json.loads(error.content)
            raise ValidationError(body["message"], code=body.get("code", "mfa_invalid"))
        if not proof:
            raise ValidationError("Authentication verification is unavailable.", code="mfa_invalid")
        clear_login_failures("django-admin", identifier)
        # auth_login may flush the previous account's session. Persist only after
        # LoginView finishes, so switching operators cannot lose the new proof.
        self.request._zitch_admin_factor_proof = proof
        record_audit("django_admin.login", actor=user, target=user.username)
        return cleaned


class ZitchAdminSite(AdminSite):
    login_form = ZitchAdminAuthenticationForm
    login_template = "admin/zitch_login.html"

    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        from accounts.models import OperatorTotp
        from admin_api.views import _mfa_required_for

        factor = OperatorTotp.objects.filter(user=request.user, confirmed=True).first()
        proof = request.session.get(SESSION_PROOF_KEY)
        if factor is None and _mfa_required_for(request.user):
            return False
        if factor is not None or proof is not None:
            expected = _factor_proof(request.user, factor)
            return bool(expected and isinstance(proof, str) and hmac.compare_digest(proof, expected))
        # Preserve existing unenrolled sessions only while the staged rollout
        # permits password-only operators. Enrolment makes proof mandatory.
        return True

    def login(self, request, extra_context=None):
        if request.method == "POST":
            return self._post_login(request, extra_context)
        return super().login(request, extra_context)

    @method_decorator(ratelimit("django_admin_login", limit=10, window=300))
    def _post_login(self, request, extra_context=None):
        response = super().login(request, extra_context)
        proof = getattr(request, "_zitch_admin_factor_proof", "")
        if response.status_code == 302 and request.user.is_authenticated and proof:
            request.session[SESSION_PROOF_KEY] = proof
        return response
