from django.contrib import admin

from .models import ExamProduct


@admin.register(ExamProduct)
class ExamProductAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "description", "price", "service_id", "active")
    list_filter = ("active",)
    search_fields = ("code", "name")
