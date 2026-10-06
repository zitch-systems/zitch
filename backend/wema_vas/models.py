import re
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class MigrationApproval(models.Model):
    """Retained operator evidence of an approved, zero-balance legacy cutover."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vas_migration_approval")
    reference = models.CharField(max_length=160)
    approved_by = models.CharField(max_length=160)
    legacy_account_number = models.CharField(max_length=10)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("VAS migration approvals are immutable")
        if not all((self.reference.strip(), self.approved_by.strip())) or not re.fullmatch(r"[0-9]{10}", self.legacy_account_number):
            raise ValidationError("Migration approval requires account and approval evidence")
        return super().save(*args, **kwargs)


class VirtualAccount(models.Model):
    VALIDATION, LIVE = "validation", "live"
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vas_accounts")
    number = models.CharField(max_length=10, unique=True)
    display_name = models.CharField(max_length=160)
    encrypted_identity = models.TextField()
    verification_reference = models.CharField(max_length=160)
    consent_reference = models.CharField(max_length=160)
    cutover_reference = models.CharField(max_length=160, blank=True)
    verified_at = models.DateTimeField()
    mode = models.CharField(max_length=10, choices=[(VALIDATION, VALIDATION), (LIVE, LIVE)])
    prefix = models.CharField(max_length=3)
    active = models.BooleanField(default=True)
    block_reason = models.CharField(max_length=200, blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    # Bank test notifications must never create spendable customer funds.
    validation_balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "mode"], name="vas_user_mode_unique"),
            models.CheckConstraint(condition=models.Q(validation_balance__gte=0), name="vas_validation_balance_positive"),
            models.CheckConstraint(condition=models.Q(mode="validation") | models.Q(validation_balance=0), name="vas_live_no_validation_balance"),
            models.CheckConstraint(condition=(models.Q(mode="validation", prefix="711") |
                (models.Q(mode="live") & ~models.Q(prefix="711"))), name="vas_mode_prefix_separate"),
        ]

    def clean(self):
        super().clean()
        if (not re.fullmatch(r"[0-9]{3}", self.prefix or "")
                or not re.fullmatch(re.escape(self.prefix) + r"[0-9]{7}", self.number or "")
                or (self.mode == self.VALIDATION and self.prefix != "711")
                or (self.mode == self.LIVE and self.prefix == "711")
                or self.mode not in (self.VALIDATION, self.LIVE)):
            raise ValidationError("Invalid virtual account prefix or mode")
        if (not self.display_name.startswith("Zitch/") or len(self.display_name) <= len("Zitch/")
                or not all((self.encrypted_identity, self.verification_reference, self.consent_reference, self.verified_at))):
            raise ValidationError("Virtual accounts require verified identity and consent evidence")

    def save(self, *args, **kwargs):
        self.clean()
        if self.pk:
            fields = ("user_id", "number", "mode", "prefix", "display_name", "cutover_reference", "created")
            prior = type(self).objects.filter(pk=self.pk).values(*fields).first()
            if prior and any(getattr(self, key) != prior[key] for key in fields):
                raise ValidationError("Virtual account ownership and bank identity are immutable")
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"VAS {self.pk} ({self.mode})"


class Receipt(models.Model):
    CREDITED, VALIDATION, HELD = "credited", "validation", "held"
    account = models.ForeignKey(VirtualAccount, on_delete=models.PROTECT, related_name="receipts")
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    session_id = models.CharField(max_length=128, unique=True)
    payment_reference = models.CharField(max_length=128, unique=True)
    fingerprint = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    source_account = models.CharField(max_length=10)
    source_bank = models.CharField(max_length=120)
    occurred_at = models.DateTimeField()
    received_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField(max_length=10, choices=[(value, value) for value in (CREDITED, VALIDATION, HELD)])
    transaction = models.OneToOneField("wallet.Transaction", on_delete=models.PROTECT, null=True, blank=True, related_name="vas_receipt")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="vas_receipt_amount_positive"),
            models.CheckConstraint(condition=(models.Q(state="credited", transaction__isnull=False) |
                models.Q(state__in=["held", "validation"], transaction__isnull=True)), name="vas_receipt_ledger_binding"),
        ]
        indexes = [models.Index(fields=["account", "occurred_at"], name="vas_receipt_history_idx")]

    @property
    def held(self):
        return self.state == self.HELD

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("VAS receipts are append-only; held funds require reviewed resolution")
        return super().save(*args, **kwargs)
