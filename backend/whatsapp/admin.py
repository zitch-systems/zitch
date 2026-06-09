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
    list_display = ("created", "direction", "msisdn", "text", "flagged")
    search_fields = ("msisdn", "text", "wa_message_id")
    list_filter = ("direction", "flagged")


@admin.register(PendingAction)
class PendingActionAdmin(admin.ModelAdmin):
    list_display = ("msisdn", "user", "action_type", "state", "expires_at", "created")
    search_fields = ("msisdn", "user__phone")
    list_filter = ("action_type", "state")
    raw_id_fields = ("user",)


from .models import AuditLog, Broadcast, BroadcastRecipient, ConversationState  # noqa: E402


@admin.register(ConversationState)
class ConversationStateAdmin(admin.ModelAdmin):
    list_display = ("msisdn", "status", "ai_enabled", "assigned_agent", "updated")
    list_filter = ("status", "ai_enabled")
    search_fields = ("msisdn",)
    raw_id_fields = ("assigned_agent",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created", "actor_type", "actor_id", "action", "target")
    list_filter = ("actor_type", "action")
    search_fields = ("actor_id", "action", "target")
    readonly_fields = ("actor_type", "actor_id", "action", "target", "before", "after", "created")


class BroadcastRecipientInline(admin.TabularInline):
    model = BroadcastRecipient
    extra = 0
    readonly_fields = ("user", "wa_msisdn", "status", "wa_message_id", "error", "created")
    can_delete = False


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ("created", "template_name", "category", "status",
                    "count_queued", "count_sent", "count_delivered", "count_read", "count_failed")
    list_filter = ("category", "status")
    search_fields = ("template_name",)
    inlines = [BroadcastRecipientInline]
    raw_id_fields = ("created_by",)
