from django.conf import settings
from django.db import models


class LinkedBankAccount(models.Model):
    """An external bank account a user linked via Mono open banking.

    Stores Mono's stable account id plus a cached snapshot (bank, number, name,
    balance) for display, so the wallet UI can render without a provider round
    trip on every load. Money never sits here — funding flows through the wallet
    ledger (FundingIntent + settle_funding); this only references the source.
    """

    ACTIVE = "active"
    UNLINKED = "unlinked"
    STATUSES = [(ACTIVE, ACTIVE), (UNLINKED, UNLINKED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="linked_banks")
    mono_account_id = models.CharField(max_length=64, unique=True, db_index=True)
    bank_name = models.CharField(max_length=120, blank=True, default="")
    account_number = models.CharField(max_length=20, blank=True, default="")
    account_name = models.CharField(max_length=120, blank=True, default="")
    # Cached last-known balance + when it was synced (display only).
    balance = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    balance_updated = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=ACTIVE)
    meta = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} · {self.bank_name} ****{self.account_number[-4:]}"

    @property
    def masked_number(self) -> str:
        return f"****{self.account_number[-4:]}" if self.account_number else ""
