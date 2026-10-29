from django.conf import settings
from django.db import models


class BankAccount(models.Model):
    """A verified 9PSB attachment; previous-bank numbers are never adopted."""
    wallet = models.OneToOneField("wallet.Wallet", on_delete=models.PROTECT, related_name="bank_account")
    number = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=160)
    encrypted_profile = models.TextField(blank=True)
    environment = models.CharField(max_length=10, choices=[("sandbox", "Sandbox"), ("production", "Production")])
    tier = models.PositiveSmallIntegerField(default=0)
    # Unknown restrictions fail closed; never treat absence as PND removed.
    pnd = models.BooleanField(null=True, default=None)
    frozen = models.BooleanField(null=True, default=None)
    available_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    ledger_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    maximum_deposit = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    maximum_balance = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    status = models.CharField(max_length=24, default="pending")
    opening_review_required = models.BooleanField(default=True)
    checked_at = models.DateTimeField(null=True)
    created = models.DateTimeField(auto_now_add=True)


class PreviousBankArchive(models.Model):
    """Immutable old account attachment and ring-fenced book liability.

    The global ledger is unchanged. This amount is excluded from spendable 9PSB
    funds and remains included in the platform's total customer liabilities.
    It cannot be released by a provider switch or a new account's first inflow.
    """
    wallet = models.OneToOneField("wallet.Wallet", on_delete=models.PROTECT, related_name="previous_bank_archive")
    account_number = models.CharField(max_length=20)
    account_name = models.CharField(max_length=160, blank=True)
    bank_name = models.CharField(max_length=80, blank=True)
    account_reference = models.CharField(max_length=64, blank=True)
    balance = models.DecimalField(max_digits=14, decimal_places=2)
    account_metadata = models.JSONField(default=dict)
    pending_transactions = models.PositiveIntegerField(default=0)
    review_required = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Archived banking evidence is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Archived banking evidence is immutable")


class IdentityChallenge(models.Model):
    environment = models.CharField(max_length=12, default="", blank=True,
        choices=[("sandbox", "Sandbox"), ("production", "Production")])
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=25, unique=True)
    kind = models.CharField(max_length=3, choices=[("bvn", "BVN"), ("nin", "NIN")])
    method = models.CharField(max_length=6, choices=[("OTP", "OTP"), ("FACIAL", "Facial")])
    identity_hash = models.CharField(max_length=64, db_index=True)
    last4 = models.CharField(max_length=4)
    # Short-lived retry material. Cleared on completion or expiry. Never logs.
    encrypted_input = models.TextField(blank=True)
    state = models.CharField(max_length=24, default="initiating")
    verified_at = models.DateTimeField(null=True)
    consumed_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    failures = models.PositiveSmallIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["user", "state", "expires_at"], name="bank_identity_pending")]


class HistoricalBalanceAdjustment(models.Model):
    """A settled historical refund remains a previous-provider liability."""
    archive = models.ForeignKey(PreviousBankArchive, on_delete=models.PROTECT, related_name="adjustments")
    transaction = models.OneToOneField("wallet.Transaction", on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Historical settlement evidence is immutable")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Historical settlement evidence is immutable")


class Instruction(models.Model):
    """Durable outbox; 'sending' is uncertain after a worker interruption."""
    transaction = models.OneToOneField("wallet.Transaction", on_delete=models.PROTECT, related_name="bank_instruction")
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    reference = models.CharField(max_length=25, unique=True)
    product = models.CharField(max_length=8, choices=[("waas", "WaaS"), ("vas", "VAS")])
    operation = models.CharField(max_length=32)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    state = models.CharField(max_length=16, default="prepared")
    request_digest = models.CharField(max_length=64)
    encrypted_request = models.TextField(blank=True)
    provider_reference = models.CharField(max_length=160, blank=True)
    receipt_pending = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    next_check = models.DateTimeField(null=True, db_index=True)
    last_error = models.CharField(max_length=80, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)


class Notification(models.Model):
    """Authenticated webhook receipt, before external confirmation and credit."""
    event = models.CharField(max_length=24)
    reference = models.CharField(max_length=160)
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True)
    payload_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default="pending")
    # The ledger credit reference derives from environment/account/session.
    transaction = models.OneToOneField("wallet.Transaction", on_delete=models.PROTECT, null=True)
    attempts = models.PositiveIntegerField(default=0)
    next_check = models.DateTimeField(null=True, db_index=True)
    last_error = models.CharField(max_length=80, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "reference", "account"], name="bank_notification_unique")]


class AddressVerification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=64, unique=True)
    provider_job = models.CharField(max_length=160, blank=True, db_index=True)
    address_digest = models.CharField(max_length=64)
    encrypted_address = models.TextField()
    encrypted_report = models.TextField(blank=True)
    state = models.CharField(max_length=24, default="pending")
    last_error = models.CharField(max_length=80, blank=True)
    checked_at = models.DateTimeField(null=True)
    created = models.DateTimeField(auto_now_add=True)


class AccountUpgrade(models.Model):
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    reference = models.CharField(max_length=25, unique=True)
    tier = models.PositiveSmallIntegerField()
    bvn_hash = models.CharField(max_length=64)
    nin_hash = models.CharField(max_length=64)
    address_verification = models.ForeignKey(AddressVerification, on_delete=models.PROTECT, null=True)
    request_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=24, default="pending")
    last_error = models.CharField(max_length=80, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    checked_at = models.DateTimeField(null=True)


class BillerValidation(models.Model):
    environment = models.CharField(max_length=12, default="", blank=True,
        choices=[("sandbox", "Sandbox"), ("production", "Production")])
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reference = models.CharField(max_length=64, unique=True)
    biller_id = models.CharField(max_length=80)
    item_id = models.CharField(max_length=80)
    customer_digest = models.CharField(max_length=64)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    encrypted_result = models.TextField()
    expires_at = models.DateTimeField()
    consumed = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)


class MaintenanceRun(models.Model):
    name = models.CharField(max_length=64)
    period = models.CharField(max_length=32)
    state = models.CharField(max_length=16, default="running")
    lease_until = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.CharField(max_length=120, blank=True)
    started = models.DateTimeField(auto_now_add=True)
    finished = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["name", "period"], name="maintenance_run_period")]
