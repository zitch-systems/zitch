import re

from django.conf import settings
from django.core.checks import Error, Warning, register
from django.core.exceptions import ImproperlyConfigured

from .config import approval_reference_present, config, enrollment_release_policy, validate_configuration


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
    policy = enrollment_release_policy(value)
    for problem in policy["errors"]:
        problems.append(Warning(problem + " Customer enrollment remains closed; bank notifications are unaffected.",
                                id="wema_vas.W001"))
    if value.get("ENABLE_ENROLLMENT") and policy["phase"] != "closed":
        if not (value.get("ENABLED") and value.get("MODE") == "live"
                and approval_reference_present(value.get("LIVE_APPROVAL_REFERENCE"))
                and isinstance(value.get("COLLECTION_ACCOUNT"), str)
                and re.fullmatch(r"[0-9]{10}", value["COLLECTION_ACCOUNT"])):
            problems.append(Warning("VAS customer enrollment is closed: enabled live mode, bank approval evidence and collection account are required.",
                                    id="wema_vas.W002"))
        if policy["phase"] == "pilot" and not policy["pilot_user_ids"]:
            problems.append(Warning("The VAS pilot allowlist is empty; no customer can enroll or see a funding account.",
                                    id="wema_vas.W003"))
    return problems
