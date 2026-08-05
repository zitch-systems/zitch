import io

from django.contrib import admin, messages
from django.core.management import call_command

from .models import CablePlan, DataPlan


class _WemaCatalogueSyncMixin:
    """Run `seed_wema_plans` from the browser.

    Data and cable purchases stay on VTU.ng until a plan carries the rail's own
    packageCode, and that mapping only existed as a management command — unreachable on
    a deploy with no shell, which is exactly where it is needed. This runs the same
    command rather than reimplementing the matching, which is best-effort (price, then a
    normalised name) and deliberately reviewed rather than trusted.

    Dry-run first, always: the action reports what WOULD change so the matches can be
    read, because a wrong packageCode sells the customer a different bundle than the one
    they paid for.
    """

    def _run_seed(self, request, *, dry: bool, only: str):
        buf = io.StringIO()
        try:
            call_command("seed_wema_plans", dry_run=dry, only=only, stdout=buf, stderr=buf)
        except Exception as exc:                              # noqa: BLE001
            self.message_user(request, f"Catalogue sync failed: {exc}", messages.ERROR)
            return
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        if not lines:
            lines = ["No output — nothing matched, or the catalogue is already in sync."]
        level = messages.WARNING if dry else messages.SUCCESS
        self.message_user(request, ("DRY RUN — nothing written. " if dry else "")
                          + " | ".join(lines[:40]), level)

    @admin.action(description="Preview a Wema catalogue sync (dry run, writes nothing)")
    def preview_wema_codes(self, request, queryset):
        self._run_seed(request, dry=True, only=self.wema_sync_only)

    @admin.action(description="Apply the Wema catalogue sync (writes the rail's codes)")
    def apply_wema_codes(self, request, queryset):
        self._run_seed(request, dry=False, only=self.wema_sync_only)


@admin.register(DataPlan)
class DataPlanAdmin(_WemaCatalogueSyncMixin, admin.ModelAdmin):
    list_display = ("network", "plan_type", "name", "validity", "price", "plan_code",
                    "wema_code", "active")
    list_filter = ("network", "plan_type", "active")
    search_fields = ("name", "plan_code")
    actions = ["preview_wema_codes", "apply_wema_codes"]
    # The command syncs both catalogues; each admin scopes it to its own so the output
    # is about the rows in front of you.
    wema_sync_only = "data"


@admin.register(CablePlan)
class CablePlanAdmin(_WemaCatalogueSyncMixin, admin.ModelAdmin):
    list_display = ("provider", "name", "validity", "price", "cable_plan_code",
                    "wema_code", "active")
    list_filter = ("provider", "active")
    search_fields = ("name", "cable_plan_code")
    actions = ["preview_wema_codes", "apply_wema_codes"]
    wema_sync_only = "cable"
