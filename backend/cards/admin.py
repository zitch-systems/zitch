from django.contrib import admin

from .models import CardIssuance, VirtualCard


@admin.register(VirtualCard)
class VirtualCardAdmin(admin.ModelAdmin):
    list_display = ("user", "brand", "last4", "expiry", "balance", "status", "created")
    list_filter = ("status", "brand", "created")
    search_fields = ("user__phone", "user__email", "last4")
    readonly_fields = ("card_token", "created")


@admin.register(CardIssuance)
class CardIssuanceAdmin(admin.ModelAdmin):
    """Read-only evidence queue for ambiguous/non-terminal issuer calls."""

    list_display = ("reference", "user", "provider", "state",
                    "provider_reference", "created", "updated")
    list_filter = ("state", "provider", "created")
    search_fields = ("reference", "provider_reference", "user__phone", "user__email")
    readonly_fields = (
        "user", "idempotency_key_hash", "reference", "provider", "state",
        "provider_reference", "provider_status", "message", "card", "created", "updated",
    )
    date_hierarchy = "created"

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
