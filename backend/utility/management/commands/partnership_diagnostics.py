"""Read-only deployment evidence for the sole active banking integration."""
import json
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Q

from utility import providers, wema
from utility.launch_readiness import financial_controls_report
from utility.liveness import tier2_face_available
from utility.partnership_configuration import retired_account_configuration
from wallet.funding_holds import DestinationDigest, funding_inventory
from wallet.models import UnsettledFunding, Wallet
from wallet.partnership import retired_wallet_markers


def account_inventory(source):
    """Count local active mappings separately from retained provider records."""
    numbered = Wallet.objects.exclude(account_number="")
    retired = (numbered.alias(destination_digest=DestinationDigest("account_number"))
               .filter(retired_wallet_markers() | Q(account_number__startswith="711")
                       | Q(destination_digest__in=UnsettledFunding.objects.values("destination_digest"))))
    tables = set(connection.introspection.table_names())
    retired_tables = {}
    with connection.cursor() as cursor:
        for table in ("banking_bankaccount", "wema_vas_virtualaccount"):
            if table not in tables:
                retired_tables[table] = {"records": 0, "active_wallet_matches": 0, "source_match": False}
                continue
            cursor.execute(f'SELECT COUNT(*), COUNT(DISTINCT w.id) FROM "{table}" a '
                           'LEFT JOIN wallet_wallet w ON w.account_number = a.number '
                           "AND w.account_number <> ''")
            records, matches = cursor.fetchone()
            cursor.execute(f'SELECT EXISTS(SELECT 1 FROM "{table}" WHERE number = %s)', [source])
            retired_tables[table] = {"records": records, "active_wallet_matches": matches,
                                     "source_match": bool(source and cursor.fetchone()[0])}
    return {"wallets_with_primary_numbers": numbered.count(),
            "wallets_with_retired_markers": retired.count(),
            "wallets_with_secondary_account_data": Wallet.objects.exclude(bank_accounts=[]).count(),
            "retired_provider_tables": retired_tables}


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
            "wema_connection": {
                "channel_id_configured": bool(bank.get("CHANNEL_ID")),
                "wallet_key_configured": bool((bank.get("KEYS") or {}).get("wallet")),
                "pool_account_configured": bool(bank.get("SOURCE_ACCOUNT")),
                "base_host": urlsplit(str(bank.get("BASE_URL") or "")).hostname,
                "product_keys_configured": {name: bool(value)
                                            for name, value in (bank.get("KEYS") or {}).items()},
            },
            "simulation": bool(bank.get("SIMULATION")),
            "bank_biller_enabled": providers.biller_enabled(),
            "tier2_session_adapter_available": tier2_face_available(),
            "flow_configured": bool(flow.get("FLOW_ID")),
            "flow_id": flow_id if re.fullmatch(r"[0-9]{1,30}", flow_id) else None,
            "flow_private_key_configured": bool(flow.get("PRIVATE_KEY")),
            "financial_controls": financial,
            "account_inventory": account_inventory(str(bank.get("SOURCE_ACCOUNT") or "").strip()),
            "historical_funding": funding_inventory(),
            "retired_account_configuration": retired_account_configuration(),
            "release_authorization": False,
            "limitation": "This inspection does not verify bank acceptance, OTP, live payments, "
                          "published Flow contents, physical devices or recovery.",
        }
        self.stdout.write(json.dumps(report, sort_keys=True))
        if options["require_clean_config"] and not report["retired_account_configuration"]["clean"]:
            raise CommandError("Retired account configuration remains; see variable names above.")
