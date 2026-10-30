"""Bounded recovery of persisted instructions; never invent bank confirmations."""
import logging
import time
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .accounts import complete_account, update_account
from .client import ProviderError, configured, config
from .models import AccountUpgrade, BankAccount, BillerValidation, IdentityChallenge, Instruction, Notification
from .notifications import process
from .payments import requery
from .upgrades import refresh_upgrade

log = logging.getLogger(__name__)


def sweep(*, limit=10, budget_seconds=45):
    limit = max(1, min(int(limit), 100))
    deadline = time.monotonic() + max(1, min(float(budget_seconds), 120))
    now = timezone.now()
    result = {"instructions": 0, "notifications": 0, "accounts": 0, "identities": 0, "upgrades": 0, "errors": 0}
    # Only unauthenticated/failed inputs expire at the OTP deadline. Verified
    # account-opening material is needed to recover a lost bank response.
    IdentityChallenge.objects.filter(expires_at__lte=now, verified_at__isnull=True).exclude(
        encrypted_input="").update(encrypted_input="", state="expired")
    BillerValidation.objects.filter(expires_at__lte=now).exclude(encrypted_result="").update(encrypted_result="")
    if not configured():
        return result
    environment = config().get("ENVIRONMENT", "sandbox")
    queues = [
        ("notifications", Notification.objects.filter(account__environment=environment, next_check__lte=now).exclude(state="complete").order_by("next_check"), process),
        ("instructions", Instruction.objects.filter(account__environment=environment, next_check__lte=now).filter(
            Q(state__in=["prepared", "sending", "pending"]) | Q(state="success", receipt_pending=True)).order_by("next_check"), requery),
        ("identities", IdentityChallenge.objects.filter(environment=environment, state__in=["verified", "opening"], verified_at__isnull=False).order_by("updated"), complete_account),
        ("accounts", BankAccount.objects.filter(environment=environment).filter(Q(checked_at__lt=now-timedelta(minutes=4)) | Q(checked_at__isnull=True)).order_by("checked_at"), update_account),
        ("upgrades", AccountUpgrade.objects.filter(account__environment=environment, state__in=["pending", "submitted", "review"]).filter(Q(checked_at__lt=now-timedelta(minutes=5)) | Q(checked_at__isnull=True)).order_by("checked_at"), lambda row: refresh_upgrade(row.account)),
    ]
    for kind, rows, handler in queues:
        for row in rows[:limit]:
            if time.monotonic() >= deadline:
                return result
            try:
                handler(row)
                result[kind] += 1
                if kind == "instructions":
                    _alert_stuck(row, now)
            except Exception as exc:
                # Persisted work stays queued. One malformed row or integration
                # failure must not prevent recovery for the remaining customers.
                result["errors"] += 1
                log.error("bank_recovery_failed kind=%s exception=%s", kind, type(exc).__name__)
    return result


def _alert_stuck(instruction, now):
    from utility.alerts import alert
    from utility.reconciliation import alert_due
    instruction.refresh_from_db(fields=["state", "created", "receipt_pending"])
    if (instruction.state in {"prepared", "sending", "pending"}
            and instruction.created <= now - timedelta(hours=2)
            and alert_due(f"9psb-stuck-instruction:{instruction.pk}", [str(instruction.pk)])):
        alert("9PSB payment still pending after two hours; review the bank instruction. "
              "No automatic refund was made.", level="error", instruction_id=instruction.pk)
