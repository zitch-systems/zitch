from django.contrib import admin

from .models import FundingIntent, Transaction, Wallet


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


@admin.register(FundingIntent)
class FundingIntentAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "amount", "status", "credited", "created")
    list_filter = ("status", "credited", "created")
    search_fields = ("reference", "user__phone", "user__email")
    readonly_fields = ("reference", "created", "updated")
