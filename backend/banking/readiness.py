"""Redacted configuration/readiness evidence; no external side effects."""
from urllib.parse import urlsplit
from .client import config, configured, sandbox
from .fees import transfer_fee


def report():
    conf = config()
    gaps = []
    if conf.get("ENVIRONMENT") not in {"sandbox", "production"}:
        gaps.append("invalid_environment")
    if not configured():
        gaps.append("waas_credentials_missing")
    if not configured("vas"):
        gaps.append("vas_credentials_missing")
    if not conf.get("WEBHOOK_USERNAME") or len(conf.get("WEBHOOK_PASSWORD", "")) < 24:
        gaps.append("webhook_credentials_missing_or_weak")
    if transfer_fee(1) is None:
        gaps.append("all_inclusive_transfer_fee_unconfirmed")
    if sandbox():
        gaps.append("sandbox_only")
    elif urlsplit(conf.get("BASE_URL", "")).scheme != "https":
        gaps.append("production_https_required")
    if not conf.get("UAT_APPROVED"):
        gaps.append("bank_uat_and_icad_approval_required")
    # These contracts are absent from the supplied materials. A configuration
    # boolean cannot substitute for an implemented and tested contract.
    gaps.extend(["history_and_reversal_contract_unconfirmed", "address_terminal_report_contract_unconfirmed",
                 "virtual_account_and_acquiring_contracts_missing"])
    return {"provider": "9psb", "environment": conf.get("ENVIRONMENT", "sandbox"),
        "waas_configured": configured(), "vas_configured": configured("vas"),
        "enabled": conf.get("ENABLED") is True, "vas_enabled": conf.get("VAS_ENABLED") is True,
        "production_ready": not gaps, "blockers": gaps}
