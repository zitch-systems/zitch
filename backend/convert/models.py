from django.conf import settings
from django.db import models


class ConversionRequest(models.Model):
    """Record of an airtime-to-cash conversion.

    The user sends/declares airtime from a SIM; we credit their Zitch wallet the
    cash value (airtime_amount × rate). One row per conversion, linked to the
    credit ledger row via `reference`.
    """

    PENDING = "Pending"
    SUCCESS = "Successful"
    FAILED = "Failed"
    STATUSES = [(PENDING, PENDING), (SUCCESS, SUCCESS), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversions")
    network = models.CharField(max_length=20)            # network id ("1".."4")
    phone = models.CharField(max_length=20)              # the SIM the airtime comes from
    airtime_amount = models.DecimalField(max_digits=14, decimal_places=2)
    rate = models.DecimalField(max_digits=5, decimal_places=4)  # payout fraction, e.g. 0.8000
    payout_amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} · ₦{self.airtime_amount} → ₦{self.payout_amount} ({self.status})"
