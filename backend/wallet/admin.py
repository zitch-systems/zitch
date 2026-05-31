from django.contrib import admin

from .models import Transaction, Wallet


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("user", "balance", "account_number", "updated")
    search_fields = ("user__phone", "user__email", "account_number")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "service", "direction", "amount", "transaction_status", "created")
    list_filter = ("direction", "transaction_status", "created")
    search_fields = ("reference", "user__phone", "user__email", "service")
    readonly_fields = ("reference", "created")
