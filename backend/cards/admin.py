from django.contrib import admin

from .models import VirtualCard


@admin.register(VirtualCard)
class VirtualCardAdmin(admin.ModelAdmin):
    list_display = ("user", "brand", "last4", "expiry", "balance", "status", "created")
    list_filter = ("status", "brand", "created")
    search_fields = ("user__phone", "user__email", "last4")
    readonly_fields = ("card_token", "created")
