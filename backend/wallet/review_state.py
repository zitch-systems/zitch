"""Customer-safe active-review state for ledger transactions.

This intentionally returns only a short classification/reason code. Provider
payloads stay in server-side evidence records and never enter history or portal
responses.
"""
from django.db.models import Q

from .models import FundingIntent, ReversalEvidence, Transaction


def transaction_review_map(transactions) -> dict[int, tuple[str, str]]:
    """Resolve reversal, card and wallet-funding holds without per-row queries."""
    rows = list(transactions)
    if not rows:
        return {}

    by_id: dict[int, tuple[str, str]] = {}
    row_ids = {row.pk for row in rows}
    for payout_id, associated_id, reason in ReversalEvidence.objects.filter(
        Q(payout_id__in=row_ids) | Q(associated_payouts__id__in=row_ids),
        state__in=(ReversalEvidence.ACTIVE, ReversalEvidence.CONFLICT),
    ).values_list(
        "payout_id", "associated_payouts__id", "reason",
    ).distinct():
        review = ("reversal", str(reason or "bank_evidence")[:80])
        if payout_id in row_ids:
            by_id[payout_id] = review
        if associated_id in row_ids:
            by_id[associated_id] = review
    funding_by_reference = {}
    for reference, meta in FundingIntent.objects.filter(
        reference__in=[row.reference for row in rows],
        meta__funding_review__active=True,
    ).values_list("reference", "meta"):
        review = (meta or {}).get("funding_review") if isinstance(meta, dict) else {}
        funding_by_reference[reference] = str(
            (review or {}).get("reason") or "provider_evidence"
        )[:80]

    for row in rows:
        meta = row.meta if isinstance(row.meta, dict) else {}
        quarantine = meta.get("wema_reversal_quarantine")
        if isinstance(quarantine, dict) and quarantine.get("active") is True:
            by_id[row.pk] = (
                "reversal",
                str(quarantine.get("reason") or "bank_evidence")[:80],
            )
            continue
        if row.pk in by_id:
            continue
        if meta.get("card_funding") is True and (
            (row.transaction_status == Transaction.PENDING
             and bool(meta.get("reconcile")))
            or (meta.get("card_balance_review") is True
                and meta.get("card_balance_applied") is not True)
        ):
            by_id[row.pk] = ("card_funding", "issuer_evidence")
            continue
        funding_reason = funding_by_reference.get(row.reference)
        if funding_reason:
            by_id[row.pk] = ("wallet_funding", funding_reason)
    return by_id
