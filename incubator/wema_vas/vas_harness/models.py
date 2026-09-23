import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from .fixtures import CUSTOMERS


class VirtualAccount(models.Model):
    number = models.CharField(max_length=10, unique=True)
    active = models.BooleanField(default=True)
    simulated_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    block_reason = models.CharField(max_length=200, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=models.Q(simulated_balance__gte=0), name="vas_sim_balance_nonnegative",
        )]

    def save(self, *args, **kwargs):
        if self.number not in CUSTOMERS:
            raise ValidationError("Only the three synthetic fixture accounts are permitted.")
        super().save(*args, **kwargs)


class Inflow(models.Model):
    # Independent namespace: no FK, import, or write to wallet.Transaction/User.
    account = models.ForeignKey(VirtualAccount, on_delete=models.PROTECT, related_name="inflows")
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session_id = models.CharField(max_length=128, unique=True)
    payment_reference = models.CharField(max_length=128, unique=True)
    fingerprint = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source_account = models.CharField(max_length=10)
    source_bank = models.CharField(max_length=120)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    held = models.BooleanField(default=False)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="vas_sim_amount_positive")]
        indexes = [models.Index(fields=["account", "occurred_at"], name="vas_sim_history_idx")]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Simulation inflow records are append-only.")
        super().save(*args, **kwargs)
