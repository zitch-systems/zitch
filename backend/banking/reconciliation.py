"""Bounded recovery of persisted instructions; never invent bank confirmations."""
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
        ("instructions", Instruction.objects.filter(account__environment=environment, next_check__lte=now, state__in=["prepared", "sending", "pending"]).order_by("next_check"), requery),
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
            except (ProviderError, ValueError):
                result["errors"] += 1
    return result
