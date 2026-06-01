from django.contrib import admin

from .models import FixedSave


@admin.register(FixedSave)
class FixedSaveAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "principal", "interest", "duration_days", "status", "paid_out", "matures_at")
    list_filter = ("status", "paid_out", "created")
    search_fields = ("reference", "user__phone", "user__email")
    readonly_fields = ("reference", "created", "updated")
