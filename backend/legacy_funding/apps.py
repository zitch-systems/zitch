"""Historical financial evidence only; no provider or customer routes."""
from django.apps import AppConfig


class LegacyFundingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "legacy_funding"
    # Retain the database/migration identity and all protected foreign keys.
    label = "wema_vas"
    verbose_name = "Historical funding evidence"
