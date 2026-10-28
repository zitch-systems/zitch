"""Migration-only tombstone for databases upgraded from the retired integration."""
from django.apps import AppConfig


class LegacyFundingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "legacy_funding"
    # Django must recognize already-applied migrations. No runtime models exist.
    label = "wema_vas"
    verbose_name = "Retired migration history"
