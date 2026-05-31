from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import OTP, AccessToken, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("id", "phone", "email", "first_name", "last_name", "is_active", "date_joined")
    search_fields = ("phone", "email", "first_name", "last_name", "username")
    ordering = ("-date_joined",)
    fieldsets = BaseUserAdmin.fieldsets + (("Zitch", {"fields": ("phone",)}),)


@admin.register(AccessToken)
class AccessTokenAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "created")
    search_fields = ("user__phone", "user__email", "key")


@admin.register(OTP)
class OTPAdmin(admin.ModelAdmin):
    list_display = ("phone", "code", "used", "created")
    search_fields = ("phone",)
    list_filter = ("used",)
