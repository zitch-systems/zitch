from django.apps import AppConfig


class WemaVasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wema_vas"
    verbose_name = "Wema virtual accounts"

    def ready(self):
        from . import checks  # noqa: F401
