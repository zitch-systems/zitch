import re

from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured

from .config import config, validate_configuration


@register()
def vas_configuration(app_configs, **kwargs):
    problems = []
    if getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership") not in ("partnership", "wema_vas"):
        problems.append(Error("BANK_ACCOUNT_PROVIDER must be partnership or wema_vas.", id="wema_vas.E001"))
    if getattr(settings, "WEMA_PARTNERSHIP_MODE", "active") not in ("active", "archive"):
        problems.append(Error("WEMA_PARTNERSHIP_MODE must be active or archive.", id="wema_vas.E002"))
    value = config()
    if value.get("ENABLED"):
        try:
            validate_configuration()
        except ImproperlyConfigured as exc:
            problems.append(Error(str(exc), id="wema_vas.E003"))
    if value.get("ENABLE_ENROLLMENT"):
        if not (value.get("ENABLED") and value.get("MODE") == "live"
                and value.get("LIVE_APPROVAL_REFERENCE")
                and re.fullmatch(r"[0-9]{10}", value.get("COLLECTION_ACCOUNT", ""))):
            problems.append(Error("VAS enrollment requires enabled live mode, approval evidence and collection account.", id="wema_vas.E004"))
    return problems
