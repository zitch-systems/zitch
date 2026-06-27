from decimal import Decimal

from django.conf import settings
from django.db import models


class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wallet")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    # Dedicated (reserved) virtual account — a permanent NUBAN the user funds by
    # bank transfer, minted via Kora once KYC supplies a BVN/NIN. `account_number`
    # / `bank_name` are the primary account shown in the app; `bank_accounts` holds
    # the full list when Kora issues one per partner bank; `account_reference` is
    # our stable key with Kora (used to match the funding webhook back to a user).
    account_number = models.CharField(max_length=20, blank=True, default="")
    account_name = models.CharField(max_length=120, blank=True, default="")
    bank_name = models.CharField(max_length=80, blank=True, default="")
    account_reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    bank_accounts = models.JSONField(default=list, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Defence-in-depth behind the service-layer balance checks: the DB
            # itself rejects a negative balance, so no bug or race can overdraw.
            models.CheckConstraint(check=models.Q(balance__gte=0), name="wallet_balance_non_negative"),
            # A reserved (virtual) account belongs to exactly one wallet. The
            # funding webhook maps an inbound transfer to a wallet by these, so
            # the DB must guarantee they're unique — otherwise a bug or bad data
            # could credit the wrong user, or two wallets could be provisioned
            # with the same account. Scoped to non-empty so un-provisioned
            # wallets (the default "") are unconstrained.
            models.UniqueConstraint(
                fields=["account_number"],
                condition=~models.Q(account_number=""),
                name="uniq_wallet_account_number",
            ),
            models.UniqueConstraint(
                fields=["account_reference"],
                condition=~models.Q(account_reference=""),
                name="uniq_wallet_account_reference",
            ),
        ]

    def __str__(self):
        return f"{self.user} · ₦{self.balance}"


class Transaction(models.Model):
    """Append-only ledger row. One per money movement."""

    IN = "in"
    OUT = "out"
    DIRECTIONS = [(IN, "Credit"), (OUT, "Debit")]

    PENDING = "Pending"
    SUCCESS = "Successful"
    FAILED = "Failed"
    STATUSES = [(PENDING, PENDING), (SUCCESS, SUCCESS), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions")
    service = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Currency of `amount`: NGN for the primary wallet, other ISO codes for FX
    # holdings (see CurrencyWallet). Defaults NGN so every existing row is correct.
    currency = models.CharField(max_length=3, default="NGN")
    direction = models.CharField(max_length=3, choices=DIRECTIONS, default=OUT)
    transaction_status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    # Free-form details (meter token, recipient, plan, provider response…).
    meta = models.JSONField(default=dict, blank=True)
    # Client-supplied key making a spend idempotent: a retried or duplicated
    # request with the same key won't debit the wallet or call the provider
    # twice. Blank for server-originated rows (credits, settlements).
    idempotency_key = models.CharField(max_length=80, blank=True, default="", db_index=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            # The history screens and the operator portal both page a user's
            # ledger newest-first; without this every view walks the table.
            models.Index(fields=["user", "-created"], name="txn_user_created_idx"),
        ]
        constraints = [
            # Amounts are always positive; `direction` carries the sign. A DB
            # check keeps a zero/negative amount from ever entering the ledger.
            models.CheckConstraint(check=models.Q(amount__gt=0), name="txn_amount_positive"),
            # One ledger row per (user, idempotency_key) when a key is supplied —
            # the DB backstop for the dedupe, even under a concurrent race.
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_user_idempotency_key",
            ),
        ]

    def save(self, *args, **kwargs):
        """Enforce ledger immutability for the money-defining fields.

        A row's ``amount``, ``direction`` and ``currency`` are fixed at creation
        and must never change — no legitimate flow rewrites them (settlement and
        reversal only move ``transaction_status`` and annotate ``meta``). Blocking
        them here turns a bug or a stray ``Transaction.objects.get(...).save()``
        that would silently corrupt balances-vs-ledger into a loud error.

        (ORM-level guard; a queryset ``.update()`` bypasses ``save()`` — back it
        with a Postgres BEFORE UPDATE trigger in production for defence in depth.)
        """
        if self.pk:
            prior = type(self).objects.filter(pk=self.pk).values(
                "amount", "direction", "currency").first()
            if prior and (
                self.amount != prior["amount"]
                or self.direction != prior["direction"]
                or self.currency != prior["currency"]
            ):
                raise ValueError(
                    "Ledger rows are immutable: amount/direction/currency cannot change once written"
                )
        super().save(*args, **kwargs)

    def __str__(self):
        sign = "+" if self.direction == self.IN else "-"
        return f"{self.service} {sign}₦{self.amount} ({self.transaction_status})"


class FundingIntent(models.Model):
    """Tracks a wallet top-up from initialize -> verified, keyed by the payment
    reference. Crediting is idempotent: a reference can only fund the wallet once
    (guarded by `credited`), so retries/duplicate webhooks are safe.
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (PAID, PAID), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="funding_intents")
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    credited = models.BooleanField(default=False)
    # Free-form context, e.g. {"provider": "kora"} — records which rail started
    # the charge so verify confirms against the same one.
    meta = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} · ₦{self.amount} · {self.status}"


class CurrencyWallet(models.Model):
    """A non-NGN balance the user holds (USD / GBP / CAD …).

    NGN stays in `Wallet` (all existing money code uses it); this table covers FX
    holdings, one row per (user, currency). A DB check keeps balances non-negative.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="currency_wallets")
    currency = models.CharField(max_length=3)
    balance = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    updated = models.DateTimeField(auto_now=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "currency"], name="uniq_user_currency_wallet"),
            models.CheckConstraint(check=models.Q(balance__gte=0), name="currency_wallet_balance_non_negative"),
        ]

    def __str__(self):
        return f"{self.user} · {self.currency} {self.balance}"


class FxQuote(models.Model):
    """A time-boxed FX quote (Fincra). Execution is valid only until `expires_at`,
    and a `used` quote can't run again — so a stale rate is never settled and a
    quote is spent at most once (alongside the ledger idempotency key)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="fx_quotes")
    quote_ref = models.CharField(max_length=80, unique=True, db_index=True)
    from_currency = models.CharField(max_length=3)
    to_currency = models.CharField(max_length=3)
    sell_amount = models.DecimalField(max_digits=18, decimal_places=2)
    receive_amount = models.DecimalField(max_digits=18, decimal_places=2)
    rate = models.DecimalField(max_digits=18, decimal_places=8)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    @property
    def expired(self) -> bool:
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.sell_amount} {self.from_currency}->{self.to_currency} @ {self.rate}"
