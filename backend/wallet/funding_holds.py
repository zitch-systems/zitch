"""Accounting evidence for outstanding money, independent of bank integrations."""
from django.db.models import Sum
from django.db.models.functions import SHA256

from wallet.models import UnsettledFunding


class DestinationDigest(SHA256):
    """Use PostgreSQL's built-in hash without requiring the pgcrypto extension."""

    def as_postgresql(self, compiler, connection, **extra_context):
        return self.as_sql(compiler, connection,
            template="ENCODE(SHA256(CONVERT_TO(%(expressions)s, 'UTF8')), 'hex')",
            **extra_context)


def reconciliation_scope():
    """An archived liability still requires independently verified settlement."""
    count = UnsettledFunding.objects.count()
    return {"legacy_funding_accounts": count, "legacy_funding_required": bool(count),
            "legacy_funding_balance": None, "legacy_funding_verified": False if count else None}


def funding_inventory():
    holds = UnsettledFunding.objects.all()
    return {"outstanding_holds": holds.count(),
            "unarchived_holds": holds.filter(archive__isnull=True).count(),
            "outstanding_amount": str(holds.aggregate(value=Sum("amount"))["value"] or 0)}


def transaction_has_funding_hold(txn):
    """Never relabel archived money with a customer's current bank account."""
    for evidence in UnsettledFunding.objects.filter(user_id=txn.user_id).values_list("evidence", flat=True):
        if not isinstance(evidence, dict) or not isinstance(evidence.get("credits", []), list):
            return True  # Unknown evidence must not claim current-account provenance.
        if any(isinstance(row, dict) and row.get("transaction_id") == txn.pk
               for row in evidence.get("credits", [])):
            return True
    return False
