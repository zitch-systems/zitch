from django.contrib import admin

from .models import Bank, Beneficiary


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "bank_code", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")


@admin.register(Beneficiary)
class BeneficiaryAdmin(admin.ModelAdmin):
    list_display = ("name", "account_number", "bank_name", "user", "created")
    search_fields = ("name", "account_number", "user__phone")
