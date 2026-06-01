from decimal import Decimal

from django.conf import settings
from django.db import models


class VirtualCard(models.Model):
    """A virtual debit card linked to the wallet.

    Only non-sensitive presentation data is stored here (last4, expiry, brand)
    plus the issuer's card token. Real PAN/CVV live with the issuer and are
    fetched on demand for a one-time reveal — never persisted. The on-card
    balance is funded from the Zitch wallet.
    """

    ACTIVE = "active"
    FROZEN = "frozen"
    STATUSES = [(ACTIVE, "Active"), (FROZEN, "Frozen")]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cards")
    # Issuer reference for the card (Sudo/Flutterwave card id). Mock in dev.
    card_token = models.CharField(max_length=80, blank=True, default="")
    brand = models.CharField(max_length=20, default="Verve")
    last4 = models.CharField(max_length=4)
    expiry = models.CharField(max_length=5)  # MM/YY
    holder = models.CharField(max_length=80, blank=True, default="")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    @property
    def masked(self) -> str:
        return f"5061 •••• •••• {self.last4}"

    @property
    def frozen(self) -> bool:
        return self.status == self.FROZEN

    def __str__(self):
        return f"{self.user} · {self.masked} · {self.status}"
