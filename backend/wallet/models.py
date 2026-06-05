from decimal import Decimal

from django.conf import settings
from django.db import models


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    account_number = models.CharField(max_length=20, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Defence-in-depth behind the service-layer balance checks: the DB
            # itself rejects a negative balance, so no bug or race can overdraw.
            models.CheckConstraint(check=models.Q(balance__gte=0), name="wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"{self.user} · ₦{self.balance}"


class Transaction(models.Model):
    """Append-only ledger row. One per money movement."""

    IN = "in"
    OUT = "out"
    DIRECTIONS = [(IN, "Credit"), (OUT, "Debit")]

    PENDING = "Pending"
    SUCCESS = "Successful"
    FAILED = "Failed"
    STATUSES = [(PENDING, PENDING), (SUCCESS, SUCCESS), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    service = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    direction = models.CharField(max_length=3, choices=DIRECTIONS, default=OUT)
    transaction_status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    # Free-form details (meter token, recipient, plan, provider response…).
    meta = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        constraints = [
            # Amounts are always positive; `direction` carries the sign. A DB
            # check keeps a zero/negative amount from ever entering the ledger.
            models.CheckConstraint(check=models.Q(amount__gt=0), name="txn_amount_positive"),
        ]

    def __str__(self):
        sign = "+" if self.direction == self.IN else "-"
        return f"{self.service} {sign}₦{self.amount} ({self.transaction_status})"


class FundingIntent(models.Model):
    """Tracks a wallet top-up from initialize -> verified, keyed by the payment
    reference. Crediting is idempotent: a reference can only fund the wallet once
    (guarded by `credited`), so retries/duplicate webhooks are safe.
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (PAID, PAID), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funding_intents")
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    credited = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} · ₦{self.amount} · {self.status}"
