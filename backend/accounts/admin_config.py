"""Keep Django's registered admin models behind Zitch operator authentication."""
from django.contrib.admin.apps import AdminConfig


class ZitchAdminConfig(AdminConfig):
    default_site = "accounts.admin_site.ZitchAdminSite"
