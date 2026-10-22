"""Read-only deployment evidence for the sole active banking integration."""
import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from utility import providers, wema
from utility.launch_readiness import financial_controls_report
from utility.liveness import tier2_face_available
from utility.partnership_configuration import retired_account_configuration
from wallet.partnership import legacy_funding_inventory


class Command(BaseCommand):
    help = "Report redacted Partnership configuration and financial control evidence."

    def add_arguments(self, parser):
        parser.add_argument("--require-clean-config", action="store_true",
                            help="Fail if retired virtual-account settings remain deployed.")

    def handle(self, *args, **options):
        bank = settings.WEMA
        flow = getattr(settings, "WHATSAPP_FLOW", {}) or {}
        financial = financial_controls_report()
        flow_id = str(flow.get("FLOW_ID") or "")
        report = {
            "read_only": True,
            "scope": "Configuration and local evidence only; no provider calls or financial changes.",
            "account_provider": providers.bank_account_provider(),
            "identity_provider": providers.kyc_provider(),
            "partnership_active": providers.partnership_new_business_allowed(),
            "bank_configured": wema.wema_live(),
            "simulation": bool(bank.get("SIMULATION")),
            "bank_biller_enabled": providers.biller_enabled(),
            "tier2_session_adapter_available": tier2_face_available(),
            "flow_configured": bool(flow.get("FLOW_ID")),
            "flow_id": flow_id if re.fullmatch(r"[0-9]{1,30}", flow_id) else None,
            "flow_private_key_configured": bool(flow.get("PRIVATE_KEY")),
            "financial_controls": financial,
            "historical_funding": legacy_funding_inventory(),
            "retired_account_configuration": retired_account_configuration(),
            "release_authorization": False,
            "limitation": "This inspection does not verify bank acceptance, OTP, live payments, "
                          "published Flow contents, physical devices or recovery.",
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
        if options["require_clean_config"] and not report["retired_account_configuration"]["clean"]:
            raise CommandError("Retired account configuration remains; see variable names above.")
