import random

from django.conf import settings
from django.db import models


class VirtualCard(models.Model):
    """A virtual debit card linked to the wallet.

    Only non-sensitive presentation data is stored here (last4, expiry, brand).
    Real PAN/CVV live with the card issuer and are fetched on demand — never
    persisted. Issuance is mocked until an issuer (e.g. Sudo/Flutterwave) is set.
    """

    ACTIVE = "active"
    FROZEN = "frozen"
    STATUSES = [(ACTIVE, "Active"), (FROZEN, "Frozen")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards")
    brand = models.CharField(max_length=20, default="Verve")
    last4 = models.CharField(max_length=4)
    expiry = models.CharField(max_length=5)  # MM/YY
    holder = models.CharField(max_length=80, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    @classmethod
    def issue_for(cls, user) -> "VirtualCard":
        """Mock issuance: generate presentation data. Replace with the issuer
        API call (which returns a card token + last4/expiry) for production."""
        holder = (user.get_full_name() or user.phone or "Zitch User").upper()
        return cls.objects.create(
            user=user,
            brand="Verve",
            last4=f"{random.randint(0, 9999):04d}",
            expiry=f"{random.randint(1, 12):02d}/{random.randint(27, 31)}",
            holder=holder,
        )

    @property
    def masked(self) -> str:
        return f"5061 •••• •••• {self.last4}"

    def __str__(self):
        return f"{self.user} · {self.masked} · {self.status}"
