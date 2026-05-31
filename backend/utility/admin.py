from django.contrib import admin

from .models import CablePlan, DataPlan


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
