import json
import logging

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils import timezone

from .models import OTP, AccessToken, User

log = logging.getLogger("zitch.security")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("id", "phone", "email", "first_name", "last_name", "is_active", "date_joined")
    search_fields = ("phone", "email", "first_name", "last_name", "username")
    ordering = ("-date_joined",)
    fieldsets = BaseUserAdmin.fieldsets + (("Zitch", {"fields": ("phone",)}),)
    actions = ["dsr_export", "dsr_request_erasure", "dsr_carry_out_erasure"]

    # NDPR/GDPR data-subject requests. `manage.py data_subject_request` is the original
    # and stays the reference implementation; these exist because it needs a production
    # shell, and a deploy without one (Render's free tier has none) could not service a
    # request at all — which is not an answer a regulator accepts for an obligation with
    # a 30-day statutory clock.
    #
    # The command's stated reason for being shell-only is sound and is preserved here:
    # an export is the highest-value single object this system can produce, so a stolen
    # operator session must not be able to exfiltrate it. It is therefore never rendered
    # in the browser or returned in a response — it is emailed to COMPLIANCE_EXPORT_EMAIL,
    # a fixed address from the environment that the operator running the action cannot
    # choose. With that address unset the action refuses rather than falling back.

    @admin.action(description="NDPR: email this subject's data export to compliance")
    def dsr_export(self, request, queryset):
        from compliance.models import DataSubjectRequest
        from compliance.services import export_user_data, record_request
        from utility.providers import send_email

        to = (getattr(settings, "COMPLIANCE_EXPORT_EMAIL", "") or "").strip()
        if not to:
            self.message_user(
                request,
                "COMPLIANCE_EXPORT_EMAIL is not set, so there is nowhere safe to send the "
                "export. Set it in the environment; the export is deliberately not shown "
                "in the browser.", messages.ERROR)
            return
        for user in queryset:
            data = export_user_data(user)
            payload = json.dumps(data, indent=2, ensure_ascii=False)
            req = record_request(user=user, kind=DataSubjectRequest.EXPORT,
                                 actor=request.user, completed=True,
                                 outcome={"sections": sorted(data), "delivered_to": to})
            sent = send_email(
                to, f"Zitch NDPR data export — request {req.pk}",
                f"Data-subject export for {req.subject_label} (request {req.pk}, due "
                f"{req.due:%Y-%m-%d}). Sections: {', '.join(sorted(data))}.",
                attachments=[{"filename": f"zitch-dsr-{req.pk}.json", "content": payload}])
            log.info("dsr_export request=%s by=%s ok=%s", req.pk, request.user.pk,
                     sent.get("success"))
            if sent.get("success"):
                self.message_user(
                    request,
                    f"{req.subject_label}: export emailed to {to} "
                    f"(request {req.pk}, due {req.due:%Y-%m-%d}).", messages.SUCCESS)
            else:
                # The request row stays — the export WAS produced, and an examiner asks
                # when it was run, not whether the mail server was up.
                self.message_user(
                    request,
                    f"{req.subject_label}: export recorded as request {req.pk} but the "
                    f"email failed ({sent.get('message', 'unknown error')}). Fix delivery "
                    f"and re-run; do not treat this as delivered.", messages.ERROR)

    @admin.action(description="NDPR: request erasure (step 1 of 2)")
    def dsr_request_erasure(self, request, queryset):
        """Record the erasure request without performing it.

        Erasure is irreversible, and the command guarded it with --confirm typed by the
        same person. Two admin actions do better than reproducing that: the request is
        recorded first, and step 2 refuses to act on a request raised by the same
        operator — so an erasure needs two people, and the row naming both is the
        artifact an examiner asks for.
        """
        from compliance.models import DataSubjectRequest
        from compliance.services import record_request

        for user in queryset:
            if DataSubjectRequest.objects.filter(
                    user=user, kind=DataSubjectRequest.ERASE,
                    status=DataSubjectRequest.RECEIVED).exists():
                self.message_user(request, f"{user}: an erasure request is already open.",
                                  messages.WARNING)
                continue
            req = record_request(user=user, kind=DataSubjectRequest.ERASE,
                                 actor=request.user, completed=False)
            self.message_user(
                request,
                f"{req.subject_label}: erasure requested (request {req.pk}, due "
                f"{req.due:%Y-%m-%d}). A DIFFERENT operator must now run step 2.",
                messages.SUCCESS)

    @admin.action(description="NDPR: carry out a requested erasure (step 2 of 2)")
    def dsr_carry_out_erasure(self, request, queryset):
        """Perform an erasure that someone else requested.

        Clears identifiers and deactivates the account; the ledger, audit log and any AML
        cases are RETAINED — the 5+ year record-keeping commitment is not ours to waive,
        which is why erase_user_data pseudonymises rather than deletes.
        """
        from compliance.models import DataSubjectRequest
        from compliance.services import erase_user_data

        for user in queryset:
            req = (DataSubjectRequest.objects
                   .filter(user=user, kind=DataSubjectRequest.ERASE,
                           status=DataSubjectRequest.RECEIVED)
                   .order_by("-received").first())
            if req is None:
                self.message_user(
                    request,
                    f"{user}: no open erasure request — run step 1 first. Erasure is "
                    f"irreversible, so it is never a single click.", messages.ERROR)
                continue
            if req.requested_by_id == request.user.pk:
                self.message_user(
                    request,
                    f"{user}: you raised request {req.pk} yourself. A second operator has "
                    f"to carry it out.", messages.ERROR)
                continue
            # Captured before the erasure: afterwards there is nothing left to derive it
            # from, and the request has to stay traceable to the person it concerned.
            label = user.email or user.phone or user.username or f"u_{user.pk}"
            outcome = erase_user_data(user)
            req.subject_label = req.subject_label or label
            req.outcome = outcome
            req.status = DataSubjectRequest.COMPLETED
            req.completed = timezone.now()
            req.save(update_fields=["subject_label", "outcome", "status", "completed"])
            log.info("dsr_erase request=%s by=%s requested_by=%s", req.pk, request.user.pk,
                     req.requested_by_id)
            self.message_user(
                request,
                f"{label}: erased (request {req.pk}). Pseudonym {outcome['pseudonym']}; "
                f"cleared {', '.join(outcome['cleared_fields']) or 'nothing'}. Ledger, "
                f"audit log and AML cases retained.", messages.SUCCESS)


@admin.register(AccessToken)
class AccessTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "masked_key", "created")
    search_fields = ("user__phone", "user__email")
    readonly_fields = ("created",)

    @admin.display(description="token")
    def masked_key(self, obj):
        # Never surface a full live bearer token in the admin (it could be
        # lifted to impersonate the user); show only a short prefix.
        return f"{obj.key[:6]}…" if obj.key else ""


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    # Never surface the code (it is stored only as a hash, and showing a live code
    # to staff would defeat the point). Purpose + used are enough to triage.
    list_display = ("phone", "purpose", "used", "created")
    search_fields = ("phone",)
    list_filter = ("used", "purpose")
