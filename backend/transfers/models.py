from django.conf import settings
from django.db import models


class Bank(models.Model):
    """A payout bank the user can send to."""

    code = models.CharField(max_length=20, unique=True)   # e.g. "gtb"
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=9, blank=True, default="")
    # NIBSS / payout-provider bank code, used when live.
    bank_code = models.CharField(max_length=10, blank=True, default="")
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Beneficiary(models.Model):
    """A saved transfer recipient. Auto-created on first transfer; deduped per
    user by (account_number, bank_name)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries")
    name = models.CharField(max_length=80)
    account_number = models.CharField(max_length=20)
    bank_name = models.CharField(max_length=60)
    bank_code = models.CharField(max_length=20, blank=True, default="")
    color = models.CharField(max_length=9, blank=True, default="#0FA295")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        unique_together = [("user", "account_number", "bank_name")]

    @property
    def initials(self) -> str:
        return "".join(w[0] for w in self.name.split()[:2]).upper() or "ZT"

    def __str__(self):
        return f"{self.name} · {self.account_number} ({self.bank_name})"
