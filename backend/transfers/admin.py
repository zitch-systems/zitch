from django.contrib import admin, messages

from .models import Bank, Beneficiary


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "bank_code", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")
    actions = ["sync_bank_codes"]

    @admin.action(description="Sync bank codes from the 9PSB bank directory")
    def sync_bank_codes(self, request, queryset):
        """Run `manage.py sync_bank_directory` for an operator with no shell.

        Deliberately ignores the row selection: a partial sync would leave the
        picker half in one code space and half in the other. The command changes
        nothing when the bank cannot confirm its directory.
        """
        from io import StringIO

        from django.core.management import CommandError, call_command

        out = StringIO()
        try:
            call_command("sync_bank_directory", stdout=out)
        except CommandError as exc:
            self.message_user(request, f"{exc}. No code was changed.", messages.ERROR)
            return
        self.message_user(request, out.getvalue().strip() or "Bank directory synced.",
                          messages.SUCCESS)


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ("name", "account_number", "bank_name", "user", "created")
    search_fields = ("name", "account_number", "user__phone")
