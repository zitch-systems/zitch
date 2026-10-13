from django.apps import AppConfig


class WhatsappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "whatsapp"

    def ready(self):
        # Register durable maker/checker executors at process startup. Keeping
        # this out of a view means workers and management commands see the same
        # action registry as the HTTP process.
        from . import approval_actions  # noqa: F401
