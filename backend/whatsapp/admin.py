from django.contrib import admin

from .models import PendingAction, WaMessageLog, WhatsAppLink


@admin.register(WhatsAppLink)
class WhatsAppLinkAdmin(admin.ModelAdmin):
    list_display = ("wa_msisdn", "user", "status", "ai_enabled", "marketing_opt_in", "linked_at")
    search_fields = ("wa_msisdn", "user__phone", "user__email", "link_code")
    list_filter = ("status", "ai_enabled", "marketing_opt_in")
    raw_id_fields = ("user",)


@admin.register(WaMessageLog)
class WaMessageLogAdmin(admin.ModelAdmin):
    """The message log, plus the inbound queue's processing state.

    The queue columns are here because "the bot isn't replying" is answered by them
    and nowhere else: an inbound row with no `processed_at` is a message Meta
    delivered and we accepted but never acted on. A screenful of those means the
    worker service is down, not that the router is broken.
    """

    list_display = ("created", "direction", "msisdn", "text", "queue_state", "flagged")
    search_fields = ("msisdn", "text", "wa_message_id")
    list_filter = ("direction", "flagged", "processed_at")
    readonly_fields = ("processing_attempts", "processing_started_at", "processed_at",
                       "next_attempt_at", "processing_error")
    actions = ["process_now"]

    @admin.display(description="Queue")
    def queue_state(self, obj):
        if obj.direction != WaMessageLog.IN:
            return "—"
        if obj.processing_error.startswith("dead_letter"):
            return f"✗ gave up ({obj.processing_error})"
        if obj.processed_at:
            return "✓ processed"
        return f"⏳ waiting (attempt {obj.processing_attempts})"

    @admin.action(description="Process now (run the queued message through the bot)")
    def process_now(self, request, queryset):
        """Drain selected inbound messages in-process.

        The worker normally does this. When it is down — and there is no shell to
        start it from — this is how a stuck conversation gets its reply without
        waiting for the service to come back. It runs the same job function, so
        leases, retries and dead-lettering behave identically; re-running an
        already-processed row is a no-op.
        """
        from .jobs import process_inbound_message

        counts = {}
        for row in queryset.filter(direction=WaMessageLog.IN):
            outcome = process_inbound_message(row.pk)
            counts[outcome] = counts.get(outcome, 0) + 1
        if not counts:
            self.message_user(request, "Nothing to do — outbound rows are not queued work.")
            return
        self.message_user(
            request, "; ".join(f"{n} {outcome}" for outcome, n in sorted(counts.items())))


@admin.register(PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    list_display = ("msisdn", "user", "action_type", "state", "expires_at", "created")
    search_fields = ("msisdn", "user__phone")
    list_filter = ("action_type", "state")
    raw_id_fields = ("user",)


from .models import (AuditLog, Broadcast, BroadcastRecipient,  # noqa: E402
                     ConversationState, WebhookEvent)


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    """Read-only, like AuditLog: the value of a forensic log is that nobody can edit
    it after the fact — including a superuser reading it in the admin."""

    list_display = ("created", "source", "outcome", "verified", "http_status",
                    "remote_ip", "reference", "action")
    list_filter = ("source", "outcome", "verified")
    search_fields = ("reference", "remote_ip", "action")
    readonly_fields = ("source", "outcome", "verified", "http_status", "remote_ip",
                       "reference", "action", "payload", "created")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ConversationState)
class ConversationStateAdmin(admin.ModelAdmin):
    list_display = ("msisdn", "status", "ai_enabled", "assigned_agent", "updated")
    list_filter = ("status", "ai_enabled")
    search_fields = ("msisdn",)
    raw_id_fields = ("assigned_agent",)
    actions = ["return_to_bot"]

    @admin.action(description="Return to bot (un-mute the automated replies)")
    def return_to_bot(self, request, queryset):
        """`status=human` silences the bot on purpose so an agent can take over. If
        the handover is never closed the conversation stays silent forever and looks
        identical, from the customer's side, to an outage. This is the same
        transition as the ops endpoint, audited the same way."""
        from .ops import record_audit

        changed = 0
        for convo in queryset.exclude(status=ConversationState.BOT):
            before = {"status": convo.status, "ai_enabled": convo.ai_enabled}
            convo.status = ConversationState.BOT
            convo.ai_enabled = True
            convo.assigned_agent = None
            convo.save(update_fields=["status", "ai_enabled", "assigned_agent"])
            record_audit("conversation.return_to_bot", actor=request.user,
                         target=f"wa:{convo.msisdn}", before=before,
                         after={"status": convo.status, "ai_enabled": True})
            changed += 1
        self.message_user(request, f"{changed} conversation(s) returned to the bot.")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created", "actor_type", "actor_id", "action", "target")
    list_filter = ("actor_type", "action")
    search_fields = ("actor_id", "action", "target")
    readonly_fields = ("actor_type", "actor_id", "action", "target", "before", "after", "created")

    # Tamper-evident: even a Django superuser can't add, edit or delete audit rows
    # from the admin — the log is append-only (matched by the model-layer guard).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class BroadcastRecipientInline(admin.TabularInline):
    model = BroadcastRecipient
    extra = 0
    readonly_fields = ("user", "wa_msisdn", "status", "wa_message_id", "error",
                       "processing_attempts", "processed_at", "created")
    can_delete = False


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ("created", "template_name", "category", "status",
                    "count_queued", "count_sent", "count_delivered", "count_read",
                    "count_failed", "count_unknown")
    list_filter = ("category", "status")
    search_fields = ("template_name",)
    inlines = [BroadcastRecipientInline]
    raw_id_fields = ("created_by",)


from .models import ApprovalRequest  # noqa: E402


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(admin.ModelAdmin):
    """The approval queue, drainable from /admin/ today.

    Deliberately NOT read-only like AuditLog and WebhookEvent: a held action has to be
    decidable, and until the portal SPA grows a screen this is the only place that can
    happen. Editing is limited to the decision fields — the action and payload are what
    was requested and must not be rewritten before approval, or the checker would be
    approving something other than what the maker submitted.
    """

    list_display = ("created", "action", "status", "requested_by", "decided_by", "reason")
    list_filter = ("status", "action")
    search_fields = ("action", "reason", "requested_by__username", "decided_by__username")
    readonly_fields = ("action", "payload", "reason", "requested_by", "created",
                       "decided", "result", "status")
    fields = readonly_fields + ("decided_by", "decision_note")

    def has_add_permission(self, request):
        # Requests are created by the endpoint that would have performed the action.
        # Hand-creating one here would skip the maker's identity and the audit row.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
