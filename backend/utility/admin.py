from django.contrib import admin

from .models import CablePlan, DataPlan, WemaBiller


# 9PSB VAS catalogues are read live from the bank. These local plan tables and
# the retired provider's biller codes are historical evidence only.
@admin.register(DataPlan)
class DataPlanAdmin(admin.ModelAdmin):
    list_display = ("network", "plan_type", "name", "validity", "price", "plan_code", "active")
    list_filter = ("network", "plan_type", "active")
    search_fields = ("name", "plan_code")


@admin.register(CablePlan)
class CablePlanAdmin(admin.ModelAdmin):
    list_display = ("provider", "name", "validity", "price", "cable_plan_code", "active")
    list_filter = ("provider", "active")
    search_fields = ("name", "cable_plan_code")


@admin.register(WemaBiller)
class RetiredBillerAdmin(admin.ModelAdmin):
    """Retired provider biller codes, retained read-only as historical evidence."""
    list_display = ("service_id", "meter_type", "package_id", "biller_id", "name", "active", "updated")
    list_filter = ("meter_type", "active")
    search_fields = ("service_id", "package_id", "name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
