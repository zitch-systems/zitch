"""Redacted configuration inventory for Partnership-only deployments."""
import os


# These names belong to Partnership airtime/data, not the retired account API.
PARTNERSHIP_PAYMENT_SETTINGS = frozenset({
    "WEMA_VAS_STATUS_LEGEND", "WEMA_VAS_REQUERY_AFTER_SECONDS",
})


def retired_account_configuration(environ=None):
    """Return names only, including empty obsolete variables; never secret values.

    Historical decryption material is retained under
    HISTORICAL_FUNDING_IDENTITY_KEYS. No active provider reads that key.
    """
    environ = os.environ if environ is None else environ
    obsolete = sorted(key for key in environ if (
        key.startswith("WEMA_VAS_") and key not in PARTNERSHIP_PAYMENT_SETTINGS
        or key.startswith("WHATSAPP_FLOW_VAS_")
        or key in {"BANK_ACCOUNT_PROVIDER", "WEMA_PARTNERSHIP_RESTORE_VAS"}
    ))
    return {"clean": not obsolete, "obsolete_variable_names": obsolete,
            "historical_decryption_key_retained": bool(
                environ.get("HISTORICAL_FUNDING_IDENTITY_KEYS"))}
