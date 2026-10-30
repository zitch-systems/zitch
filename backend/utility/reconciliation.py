"""Durable scheduling for status lookups; callbacks do not use this delay."""
from datetime import timedelta
from hashlib import sha256

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from wallet.models import Transaction


@transaction.atomic
def claim_status_lookup(txn):
    """Reserve the next lookup across cron/worker instances, without holding a
    database lock during the network request. A crashed request is retried later.
    Only scheduling metadata changes here; age never decides financial outcome.
    """
    current = Transaction.objects.select_for_update().get(pk=txn.pk)
    if current.transaction_status != Transaction.PENDING:
        return False
    meta = dict(current.meta or {})
    quarantine = meta.get("wema_reversal_quarantine") or {}
    if isinstance(quarantine, dict) and quarantine.get("active") is True:
        # A bank-history return was correlated with this payout but cannot be
        # applied safely (for example, only part returned or old code already
        # credited the inbound row).  No status poll may transition the payout
        # until an audited operator resolution clears/reclassifies the hold.
        txn.meta = meta
        return False
    retry = meta.get("wema_requery") or {}
    now = timezone.now()
    try:
        due = parse_datetime(str(retry.get("next_at") or ""))
        if due and timezone.is_naive(due):
            due = timezone.make_aware(due)
        if due and due > now:
            return False
        attempts = max(0, int(retry.get("attempts") or 0))
    except (TypeError, ValueError):
        attempts = 0
    delay = min(900, 30 * 2 ** min(attempts, 5))
    meta["wema_requery"] = {
        "attempts": attempts + 1,
        "last_at": now.isoformat(),
        "next_at": (now + timedelta(seconds=delay)).isoformat(),
    }
    current.meta = meta
    current.save(update_fields=["meta"])
    txn.meta = meta
    return True


def alert_due(kind, references):
    """Alert immediately for a changed set, then remind hourly while unresolved.
    Cache failure must not hide an operational incident.
    """
    digest = sha256("|".join(sorted(references)).encode()).hexdigest()
    try:
        return cache.add(f"wema:reconcile-alert:{kind}:{digest}", True, timeout=3600)
    except Exception:
        return True

