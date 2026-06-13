from django.contrib import admin

from .models import ConversionRequest


@admin.register(ConversionRequest)
class ConversionRequestAdmin(admin.ModelAdmin):
    list_display = ("user", "network", "airtime_amount", "payout_amount", "status", "created")
    list_filter = ("status", "network")
    search_fields = ("user__username", "user__phone", "phone", "reference")
    readonly_fields = ("created",)
