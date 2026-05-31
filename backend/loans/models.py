from decimal import Decimal

from django.conf import settings
from django.db import models


class Loan(models.Model):
    """A disbursed loan and its repayment state.

    Interest is flat: interest = principal * RATE * (tenure_days / 30).
    A user may hold only one ACTIVE loan at a time (enforced in the view).
    """

    RATE = Decimal("0.045")  # 4.5% per 30 days
    DEFAULT_LIMIT = Decimal("500000.00")

    ACTIVE = "active"
    REPAID = "repaid"
    STATUSES = [(ACTIVE, "Active"), (REPAID, "Repaid")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loans")
    principal = models.DecimalField(max_digits=14, decimal_places=2)
    interest = models.DecimalField(max_digits=14, decimal_places=2)
    tenure_days = models.PositiveIntegerField(default=30)
    amount_repaid = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    due_date = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    @property
    def total_repayment(self) -> Decimal:
        return self.principal + self.interest

    @property
    def outstanding(self) -> Decimal:
        return self.total_repayment - self.amount_repaid

    @classmethod
    def quote(cls, principal: Decimal, tenure_days: int) -> Decimal:
        """Interest for a given principal and tenure."""
        return (principal * cls.RATE * (Decimal(tenure_days) / Decimal(30))).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.user} · ₦{self.principal} · {self.status}"
