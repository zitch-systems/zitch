"""Redacted configuration/readiness evidence; no external side effects."""
from .client import Client, ProviderError, config, configured, sandbox
from .fees import transfer_fee


def endpoint_report(product):
    """Describe the resolved wire destination without I/O or secret values.

    Invalid URLs are never reflected: a mistaken query string or userinfo may
    itself contain credentials. Errors describe configuration, not bank access.
    """
    client = Client(product)
    result = {"api_base_url": None, "authentication_url": None,
              "credentials_present": False, "errors": []}
    for key, resolve in (("api_base_url", client._api_base), ("authentication_url", client._auth_url)):
        try:
            result[key] = resolve()
        except ProviderError as exc:
            if exc.code not in result["errors"]:
                result["errors"].append(exc.code)
    try:
        result["authentication_body_fields"] = sorted(client._credentials())
        result["credentials_present"] = True
    except ProviderError:
        result["errors"].append(product + "_credentials_missing")
    result["configured"] = not result["errors"]
    return result


def report():
    conf = config()
    gaps = []
    endpoints = {product: endpoint_report(product) for product in ("waas", "vas")}
    if conf.get("ENVIRONMENT") not in {"sandbox", "production"}:
        gaps.append("invalid_environment")
    for details in endpoints.values():
        gaps.extend(error for error in details["errors"] if error not in gaps)
    if not conf.get("WEBHOOK_USERNAME") or len(conf.get("WEBHOOK_PASSWORD", "")) < 24:
        gaps.append("webhook_credentials_missing_or_weak")
    if transfer_fee(1) is None:
        gaps.append("all_inclusive_transfer_fee_unconfirmed")
    if sandbox():
        gaps.append("sandbox_only")
    elif not (endpoints["waas"]["api_base_url"] or "").startswith("https://"):
        gaps.append("production_https_required")
    if not conf.get("UAT_APPROVED"):
        gaps.append("bank_uat_and_icad_approval_required")
    # These contracts are absent from the supplied materials. A configuration
    # boolean cannot substitute for an implemented and tested contract.
    gaps.extend(["history_and_reversal_contract_unconfirmed", "address_terminal_report_contract_unconfirmed",
                 "virtual_account_and_acquiring_contracts_missing"])
    return {"provider": "9psb", "environment": conf.get("ENVIRONMENT", "sandbox"),
        "endpoints": endpoints,
        "waas_configured": configured(), "vas_configured": configured("vas"),
        "enabled": conf.get("ENABLED") is True, "vas_enabled": conf.get("VAS_ENABLED") is True,
        "production_ready": not gaps, "blockers": gaps}
