from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q


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

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cards")
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
        constraints = [
            models.UniqueConstraint(fields=["user"], name="cards_one_card_per_user"),
        ]

    @property
    def masked(self) -> str:
        return f"5061 •••• •••• {self.last4}"

    @property
    def frozen(self) -> bool:
        return self.status == self.FROZEN

    def __str__(self):
        return f"{self.user} · {self.masked} · {self.status}"


class CardIssuance(models.Model):
    """Durable intent for one non-idempotent card-issuer POST.

    The provider call is made only after this row commits.  A process crash or
    lost response therefore leaves an inspectable pending row instead of making
    a retry mint a second card.  Client keys are stored only as keyed hashes.
    """

    STARTING = "starting"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STATES = [
        (STARTING, "Starting"),
        (PENDING, "Pending review"),
        (SUCCEEDED, "Succeeded"),
        (FAILED, "Failed"),
    ]
    ACTIVE_STATES = (STARTING, PENDING)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="card_issuances",
    )
    idempotency_key_hash = models.CharField(max_length=64)
    reference = models.CharField(max_length=40, unique=True)
    provider = models.CharField(max_length=20)
    state = models.CharField(max_length=12, choices=STATES, default=STARTING)
    provider_reference = models.CharField(max_length=100, blank=True, default="")
    provider_status = models.CharField(max_length=40, blank=True, default="")
    message = models.CharField(max_length=300, blank=True, default="")
    card = models.OneToOneField(
        VirtualCard,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="issuance",
    )
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key_hash"],
                name="cards_unique_issue_key_per_user",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(state__in=("starting", "pending")),
                name="cards_one_active_issuance_per_user",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "state", "created"], name="cards_issue_user_state_idx"),
        ]

    def __str__(self):
        return f"{self.reference} · {self.user} · {self.state}"
