from django.apps import AppConfig


class WalletConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wallet"

    def ready(self):
        # Importing for the side effect of connecting the credit/debit alert
        # signal. Without this the receiver is never registered and no alert is
        # ever sent — the module is not imported by any code path otherwise.
        from . import alerts  # noqa: F401
