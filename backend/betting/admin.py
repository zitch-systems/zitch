from django.contrib import admin

from .models import BettingPlatform


@admin.register(BettingPlatform)
class BettingPlatformAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "color", "service_id", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")
