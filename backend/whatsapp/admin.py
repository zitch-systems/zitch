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
