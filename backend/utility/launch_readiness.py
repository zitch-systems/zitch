"""Read-only, redacted evidence from the scheduled financial controls.

Configuration and a responding API are not proof of settlement. This report
checks the latest control result, never an older successful result, and treats
missing, stale or malformed evidence as unverified. It does not call providers,
move funds, clear review holds, or certify device/provider acceptance.
"""
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.utils import timezone


_CONTROLS = (
    ("integrity", "recon.integrity_check", timedelta(hours=26)),
    ("bank_balances", "recon.balance_check", timedelta(hours=7)),
    ("settlement", "recon.settlement_report", timedelta(hours=26)),
)


def _zero_counts(data, *keys):
    return all(type(data.get(key)) is int and data[key] == 0 for key in keys)


def _valid_result(name, data):
    if not isinstance(data, dict):
        return False
    if name == "integrity":
        return (type(data.get("wallets")) is int and data["wallets"] >= 0
                and _zero_counts(data, "mismatches"))
    if name == "bank_balances":
        try:
            tolerance = Decimal(str(data.get("tolerance")))
        except (InvalidOperation, TypeError, ValueError):
            return False
        return (tolerance.is_finite() and tolerance == 0
                and type(data.get("wallets")) is int and data["wallets"] >= 0
                and _zero_counts(data, "unreachable", "ledger_over_bank", "bank_over_ledger")
                and data.get("legacy_funding_required") is False)
    if data.get("incomplete") is not False:
        return False
    try:
        position = Decimal(str(data.get("position")))
        held = Decimal(str(data.get("held_total")))
        owed = Decimal(str(data.get("ledger_liability")))
        stored = Decimal(str(data.get("stored_liability")))
    except (InvalidOperation, TypeError, ValueError):
        return False
    return (all(value.is_finite() for value in (position, held, owed, stored))
            and position >= 0 and held >= 0 and owed >= 0 and owed == stored
            and held - owed == position)


def financial_controls_report():
    """Return counts and fixed status strings only, suitable for build logs."""
    from django.db.models import Exists, OuterRef
    from wallet.models import BankHistoryCheckpoint, Transaction, Wallet
    from wallet.services import wema_provisioned_wallets
    from wallet.legacy_funding import reconciliation_scope
    from whatsapp.models import AuditLog
    from wallet.partnership import partnership_allowed

    try:
        wallets = Wallet.objects.count()
        # Empty installations have no customer liability to reconcile. This is
        # explicitly not bank acceptance or permission to launch.
        if wallets == 0 and not Transaction.objects.exists():
            return {"status": "no_wallets", "financial_controls_ready": True,
                    "wallets": 0, "opening_reviews": 0, "checks": {}}
        cleared = BankHistoryCheckpoint.objects.filter(
            wallet_id=OuterRef("pk"), account_number=OuterRef("account_number"),
            opening_review_required=False)
        opening_reviews = wema_provisioned_wallets().annotate(
            opening_cleared=Exists(cleared)).filter(opening_cleared=False).count()
        bank_wallets = list(wema_provisioned_wallets().select_related("user"))
        comparable_wallets = sum(partnership_allowed(wallet.user) for wallet in bank_wallets)
        unverified_legacy = reconciliation_scope()["legacy_funding_required"]
        now = timezone.now()
        checks = {}
        for name, action, max_age in _CONTROLS:
            row = AuditLog.objects.filter(action=action, actor_type="system").order_by(
                "-created", "-pk").only("created", "after").first()
            if row is None:
                status = "missing"
            elif row.created > now or now - row.created > max_age:
                status = "stale"
            elif not _valid_result(name, row.after):
                status = "failed"
            elif name == "integrity" and row.after.get("wallets") != wallets:
                status = "inventory_changed"
            elif name == "bank_balances" and row.after.get("wallets") != comparable_wallets:
                status = "inventory_changed"
            elif name == "settlement" and (type(row.after.get("nuban_wallets")) is not int
                    or row.after["nuban_wallets"] != len(bank_wallets)):
                status = "inventory_changed"
            else:
                status = "passed"
            checks[name] = status
        ready = (opening_reviews == 0 and not unverified_legacy
                 and all(s == "passed" for s in checks.values()))
        return {"status": "passed" if ready else "blocked",
                "financial_controls_ready": ready, "wallets": wallets,
                "opening_reviews": opening_reviews, "unverified_legacy_funding": unverified_legacy,
                "checks": checks}
    except Exception:
        # No exception text: database/configuration errors can contain secrets.
        return {"status": "inspection_unavailable", "financial_controls_ready": False,
                "checks": {}}
