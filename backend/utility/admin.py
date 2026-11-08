from django.contrib import admin

from .models import WemaBiller


# The DataPlan and CablePlan admins were here until the VAS rail went: those
# catalogues described airtime, data and cable products nobody can buy now. The
# retired provider's biller codes stay, read-only, as historical evidence.
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
