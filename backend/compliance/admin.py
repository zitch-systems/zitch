"""Admin surfaces for the compliance records.

Editable, unlike AuditLog and WebhookEvent: a case has to be worked and a dispute has to
be answered, and until the portal SPA grows screens this is where that happens. What is
read-only is the EVIDENCE — the detection facts, the deadline and who the subject is —
because a case whose evidence can be edited before it is dismissed proves nothing.
"""
from django.contrib import admin

from .models import AmlCase, DataSubjectRequest, Dispute


@admin.register(AmlCase)
class AmlCaseAdmin(admin.ModelAdmin):
    list_display = ("detected", "trigger", "user", "status", "due", "overdue",
                    "nfiu_reference")
    list_filter = ("status", "trigger")
    search_fields = ("user__username", "user__email", "nfiu_reference", "narrative")
    readonly_fields = ("user", "trigger", "evidence", "detected", "due", "dedupe_key",
                       "closed", "closed_by")

    @admin.display(boolean=True, description="Overdue")
    def overdue(self, obj):
        return obj.overdue

    def has_add_permission(self, request):
        # Cases are opened by the scanner or by `open_case`, which stamps the detection
        # time the 30-day deadline is derived from. A hand-made row would carry a
        # deadline of whenever someone happened to create it.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataSubjectRequest)
class DataSubjectRequestAdmin(admin.ModelAdmin):
    list_display = ("received", "kind", "subject_label", "status", "due", "completed")
    list_filter = ("kind", "status")
    search_fields = ("subject_label", "note")
    readonly_fields = ("user", "subject_label", "kind", "requested_by", "outcome",
                       "received", "due", "completed", "status")

    def has_add_permission(self, request):
        # Handled by `manage.py data_subject_request`, which does the work and records
        # the outcome. A row created here would claim a request was handled without
        # anything having happened.
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ("created", "user", "reference", "reason", "status", "due", "resolved")
    list_filter = ("status", "reason")
    search_fields = ("reference", "user__username", "user__email", "detail")
    readonly_fields = ("user", "reference", "reason", "detail", "created", "due",
                       "resolved", "resolved_by")

    def has_add_permission(self, request):
        return False   # raised by the customer, against their own transaction

    def has_delete_permission(self, request, obj=None):
        return False
