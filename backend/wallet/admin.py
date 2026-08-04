from django.contrib import admin, messages

from .models import FundingIntent, Transaction, Wallet
from .services import attach_existing_bank_account, is_demo_account


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "account_number", "bank_name", "updated")
    search_fields = ("user__phone", "user__email", "account_number")
    actions = ["clear_demo_account", "reconnect_bank_account"]

    @admin.action(description="Reconnect the bank account the rail already holds")
    def reconnect_bank_account(self, request, queryset):
        """Attach the NUBAN the bank holds for a wallet that has none.

        The in-app path does this automatically when creation is refused, but it
        needs the customer to get through the setup screen. This is the same
        recovery for an operator: after clearing a test-mode account, or when a
        provisioning callback was never received, the wallet has no account number
        and every payout is refused for having no source to debit.

        Skips a wallet that already has one — replacing a live NUBAN would strand
        deposits already sent to it.
        """
        for wallet in queryset:
            attached, detail = attach_existing_bank_account(wallet.user)
            level = messages.SUCCESS if attached and attached.account_number else messages.WARNING
            self.message_user(request, f"{wallet.user}: {detail}", level)

    @admin.action(description="Clear a test-mode funding account so a real one can be issued")
    def clear_demo_account(self, request, queryset):
        """Blank a NUBAN the MOCK rail invented, so the user can be re-provisioned.

        provision_wema_account refuses to replace an existing NUBAN — rightly, since
        overwriting a real one strands money already sent to it. But that also traps
        a wallet stamped with a test-mode number: it can never receive a live NUBAN,
        and every payout it attempts is rejected by the rail. Clearing is safe for
        exactly these, and only these: the number never existed at the bank, so no
        deposit can be in flight to it.

        The balance is untouched — it is our ledger, not the bank's.
        """
        cleared = skipped = 0
        for wallet in queryset:
            if not is_demo_account(wallet):
                skipped += 1
                continue
            wallet.account_number = ""
            wallet.account_name = ""
            wallet.bank_name = ""
            wallet.account_reference = ""
            wallet.bank_accounts = []
            wallet.save(update_fields=["account_number", "account_name", "bank_name",
                                       "account_reference", "bank_accounts", "updated"])
            cleared += 1
        if cleared:
            self.message_user(
                request,
                f"Cleared {cleared} test-mode funding account(s). Those users can now be "
                f"issued a real NUBAN by redoing account setup; balances are unchanged.",
                messages.SUCCESS)
        if skipped:
            self.message_user(
                request,
                f"Left {skipped} wallet(s) alone — their account number was issued by the "
                f"real bank, and clearing it would strand deposits sent to it.",
                messages.WARNING)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "service", "direction", "amount",
                    "transaction_status", "failure_reason", "created")
    list_filter = ("direction", "transaction_status", "created")
    search_fields = ("reference", "user__phone", "user__email", "service")
    readonly_fields = ("reference", "created")

    @admin.display(description="Why it failed")
    def failure_reason(self, obj):
        """The provider's own reason for a failed debit, from meta["failure"].

        On the list page because that is where someone looks when transfers start
        failing, and on a deploy without shell or log access it is otherwise
        unreachable — every failed row looks alike.
        """
        return (obj.meta or {}).get("failure") or "—"


@admin.register(FundingIntent)
class FundingIntentAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "amount", "status", "credited", "created")
    list_filter = ("status", "credited", "created")
    search_fields = ("reference", "user__phone", "user__email")
    readonly_fields = ("reference", "created", "updated")
