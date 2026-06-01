from decimal import Decimal

from django.conf import settings
from django.db import models


class FixedSave(models.Model):
    """A locked savings plan. Principal is debited on creation; principal +
    interest is paid back to the wallet at maturity.

    Interest is simple annualised: interest = principal * rate * (days / 365).
    Funds can't be withdrawn before the maturity date (early withdrawal would
    forfeit interest — added later).
    """

    # Lock period (days) -> annual rate.
    RATES = {30: Decimal("0.12"), 90: Decimal("0.15"), 180: Decimal("0.18"), 365: Decimal("0.22")}
    MIN_PRINCIPAL = Decimal("1000")

    ACTIVE = "active"
    MATURED = "matured"
    STATUSES = [(ACTIVE, "Active"), (MATURED, "Matured")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="savings")
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest = models.DecimalField(max_digits=14, decimal_places=2)
    rate = models.DecimalField(max_digits=5, decimal_places=4)
    duration_days = models.PositiveIntegerField()
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    paid_out = models.BooleanField(default=False)
    matures_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    @property
    def maturity_value(self) -> Decimal:
        return self.principal + self.interest

    @classmethod
    def quote(cls, principal: Decimal, days: int) -> Decimal:
        rate = cls.RATES.get(days, Decimal("0"))
        return (principal * rate * (Decimal(days) / Decimal(365))).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.user} · ₦{self.principal} · {self.status}"
