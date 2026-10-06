"""Post-migration, redacted configuration inspection; never a release gate."""
import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand

from wema_vas.config import approval_reference_present, config
from wema_vas.diagnostics import readiness_report


def _present(value):
    return isinstance(value, str) and bool(value.strip())


def _choice(value, allowed, *, blank="invalid"):
    if not isinstance(value, str):
        return "invalid"
    choice = value.strip().lower()
    return choice if choice in allowed else blank if not choice else "invalid"


def deployment_report():
    """Only allowlisted enums and presence booleans leave this function."""
    try:
        vas = config()
        bank_provider = _choice(getattr(settings, "BANK_ACCOUNT_PROVIDER", "partnership"), {"partnership", "wema_vas"})
        partnership = _choice(getattr(settings, "WEMA_PARTNERSHIP_MODE", "active"), {"active", "archive"})
        kyc_selection = _choice(getattr(settings, "KYC_PROVIDER", ""), {"prembly", "wema"}, blank="auto")
        # Mirror the pure selection rule without invoking any provider client.
        kyc = "prembly" if bank_provider != "partnership" or partnership != "active" else (
            kyc_selection if kyc_selection in {"prembly", "wema"} else "wema")
        prembly = getattr(settings, "PREMBLY", {})
        termii = getattr(settings, "TERMII", {})
        wema = getattr(settings, "WEMA", {})
        whatsapp = getattr(settings, "WHATSAPP", {})
        flow = getattr(settings, "WHATSAPP_FLOW", {})
        simulation = bool(wema.get("SIMULATION", False))
        prembly_api, prembly_app = _present(prembly.get("API_KEY")), _present(prembly.get("APP_ID"))
        termii_key, termii_sender = _present(termii.get("API_KEY")), _present(termii.get("SENDER_ID"))
        current_flow, approved_flow = flow.get("FLOW_ID"), flow.get("VAS_APPROVED_FLOW_ID")
        flow_credentials = _present(current_flow) and _present(flow.get("PRIVATE_KEY"))
        channel_credentials = _present(whatsapp.get("TOKEN")) and _present(whatsapp.get("PHONE_NUMBER_ID"))
        channel_mode = _choice(whatsapp.get("MODE", ""), {"live", "sandbox", "disabled"}, blank="auto")
        live_channel = channel_credentials and channel_mode in {"live", "auto"}
        source = getattr(settings, "WEMA_VAS_BILLER_SOURCE_ACCOUNT", "")
        source_valid = isinstance(source, str) and bool(re.fullmatch(r"[0-9]{10}", source))
        approval = getattr(settings, "WEMA_VAS_BILLER_APPROVAL_REFERENCE", "")
        source_matches = source_valid and source == vas.get("COLLECTION_ACCOUNT")
        vas_mode = _choice(vas.get("MODE", "validation"), {"validation", "live"})
        stage = "controlled-live-pilot" if vas_mode == "live" else "validation"
        return {
            "status": "inspection_completed", "read_only": True, "release_authorization": False,
            "full_go_live_ready": False,
            "configuration": {
                "account_provider": bank_provider, "partnership_mode": partnership,
                "kyc_provider_setting": kyc_selection, "kyc_provider": kyc,
                "biller_provider": _choice(getattr(settings, "VAS_PROVIDER", "wema"), {"wema"}),
                "biller_mode": _choice(getattr(settings, "WEMA_BILLER_MODE", "active"), {"active", "disabled"}),
                "simulation": simulation, "vas_mode": vas_mode,
                "prembly_api_key_present": prembly_api, "prembly_app_id_present": prembly_app,
                "prembly_credentials_configured": prembly_api and prembly_app,
                "prembly_live_configuration_ready": prembly_api and prembly_app and not simulation,
                "termii_api_key_present": termii_key, "termii_sender_present": termii_sender,
                "termii_configuration_ready": termii_key and termii_sender,
                "vas_biller_enabled": getattr(settings, "WEMA_VAS_BILLER_ENABLED", False) is True,
                "vas_biller_source_present": _present(source), "vas_biller_source_format_valid": source_valid,
                "vas_biller_approval_reference_present": approval_reference_present(approval),
                "vas_biller_source_matches_collection": bool(source_matches),
                "flow_credentials_configured": flow_credentials,
                "flow_configured": bool(flow_credentials and live_channel),
                "flow_vas_enrollment_enabled": flow.get("VAS_ENROLLMENT_ENABLED") is True,
                "flow_approved_id_present": _present(approved_flow),
                "flow_current_matches_approved": bool(_present(current_flow) and _present(approved_flow)
                                                       and current_flow == approved_flow),
            },
            "vas_readiness": readiness_report(stage),
            "scope": "Configuration and local database inspection only. No provider calls, credential values, "
                     "identity values or financial changes. Provider acceptance, SMS delivery, Flow publication "
                     "and bank settlement remain unverified.",
        }
    except Exception:
        # Render build logs must never contain configuration, SQL or a stack
        # trace with secret-bearing exception text from this diagnostic surface.
        return {"status": "inspection_unavailable", "read_only": True,
                "release_authorization": False, "full_go_live_ready": False,
                "scope": "Local inspection could not complete; no provider calls or financial changes made."}


class Command(BaseCommand):
    help = "Print non-secret deployment diagnostics without authorizing release or blocking an otherwise valid build."
    requires_system_checks = []

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(deployment_report(), sort_keys=True))
