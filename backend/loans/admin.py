from django.contrib import admin

from .models import Loan


@admin.register(Loan)
class LoanAdmin(admin.ModelAdmin):
    list_display = ("reference", "user", "principal", "interest", "tenure_days", "amount_repaid", "status", "due_date")
    list_filter = ("status", "created")
    search_fields = ("reference", "user__phone", "user__email")
    readonly_fields = ("reference", "created", "updated")
