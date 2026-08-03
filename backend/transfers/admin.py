from django.contrib import admin, messages

from utility import wema

from .models import Bank, Beneficiary
from .services import apply_bank_codes, compare_bank_codes


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "bank_code", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")
    actions = ["sync_bank_codes"]

    @admin.action(description="Sync bank codes from the payout rail")
    def sync_bank_codes(self, request, queryset):
        """Take the rail's bank code for every bank whose code differs from ours.

        Ours were seeded from a NIBSS/Paystack mirror; the rail resolves recipients
        in its own code space, and a code it does not recognise fails name enquiry
        with "account enquiry failed, confirm that the account number is valid" —
        which reads as a bad account number, not a bank problem. This is the browser
        equivalent of `manage.py wema_banks_sync --apply`, for a deploy whose
        operator has no shell.

        Deliberately ignores the row selection: a partial sync would leave the
        picker half in one code space and half in the other. Ambiguous name matches
        are never auto-applied — a wrong code here misroutes real money.
        """
        res = wema.get_banks()
        if not res.get("success"):
            self.message_user(
                request,
                f"Could not reach the payout rail: {res.get('message') or 'request failed'}. "
                "No code was changed.", messages.ERROR)
            return
        if res.get("mock"):
            self.message_user(
                request,
                "The payout rail is in MOCK mode — its bank list is a stub, not the real code "
                "space. No code was changed. Set WEMA_CHANNEL_ID + WEMA_WALLET_KEY first.",
                messages.ERROR)
            return

        cmp = compare_bank_codes(res.get("banks") or [])
        if cmp["ok"]:
            self.message_user(
                request,
                f"All {len(cmp['agree'])} active bank code(s) already match the rail.",
                messages.SUCCESS)
            return

        updated = apply_bank_codes(cmp["differ"])
        if updated:
            self.message_user(request, f"Updated {updated} bank code(s) from the rail.",
                              messages.SUCCESS)
        for row in cmp["ambiguous"]:
            self.message_user(
                request,
                f"{row['name']}: the rail lists that name under several codes "
                f"({', '.join(row['rail'])}) — set it by hand.", messages.WARNING)
        if cmp["unmatched"]:
            names = ", ".join(f"{r['name']} (ours: {r['ours'] or 'blank'})"
                              for r in cmp["unmatched"])
            self.message_user(
                request,
                f"{len(cmp['unmatched'])} bank(s) matched nothing on the rail, so their codes "
                f"are unchanged and transfers to them will keep failing: {names}.",
                messages.WARNING)
            # Without the rail's leftovers this is a dead end — you cannot tell
            # whether the rail calls the bank something else or does not carry it.
            leftovers = ", ".join(f"{r['name']} = {r['code']}" for r in cmp["rail_unmatched"])
            if leftovers:
                self.message_user(
                    request,
                    f"Unmatched on the rail's side, for mapping by hand: {leftovers}",
                    messages.INFO)


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ("name", "account_number", "bank_name", "user", "created")
    search_fields = ("name", "account_number", "user__phone")
