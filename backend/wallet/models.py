from decimal import Decimal

from django.conf import settings
from django.db import models


# Money/ledger models bind to the user with PROTECT, not CASCADE: a customer is
# frozen (is_active=False), never deleted, so a single (accidental or malicious)
# User delete can't silently erase the append-only ledger / balances / liability
# history a fintech must retain. There is no user-deletion flow; PROTECT makes
# that a hard guarantee. (Ephemeral rows like FxQuote keep CASCADE.)
class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    # Dedicated (reserved) virtual account â€” a permanent NUBAN the user funds by
    # bank transfer, minted via Wema once KYC supplies a BVN/NIN. `account_number`
    # / `bank_name` are the primary account shown in the app; `bank_accounts` holds
    # the full list when the rail issues one per partner bank; `account_reference` is
    # our stable key with Wema (used to match a reconciled deposit back to a user).
    account_number = models.CharField(max_length=20, blank=True, default="")
    account_name = models.CharField(max_length=120, blank=True, default="")
    bank_name = models.CharField(max_length=80, blank=True, default="")
    account_reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    bank_accounts = models.JSONField(default=list, blank=True)
    # The tier the PARTNER BANK holds this NUBAN at (1/2/3), which is a different
    # ladder from our own KYC tier and is enforced by the bank regardless of ours.
    # 0 = not yet known: we have never read it back, so no bank cap is applied and
    # behaviour is unchanged. Synced from partner-account-kyc-status.
    bank_tier = models.PositiveSmallIntegerField(default=0)
    # False means the bank has not yet confirmed removal of the Post-No-Debit
    # restriction. The reconcile job retries these accounts until confirmation;
    # without durable state, one transient provider failure strands outgoing funds.
    pnd_lifted = models.BooleanField(default=False, db_index=True)
    # True once the bank has refused to open a second identity OTP on this NUBAN
    # because the account already exists. Wema only accepts the remaining
    # identity through the combined existing-account upgrade (BVN + NIN + live
    # selfie in one request), so the per-identity OTP path is closed for good.
    #
    # Durable because the refusal is only discoverable by asking the provider:
    # without it every "verify my identity" round re-prompts for a NIN that
    # cannot be submitted, and the customer is told so only AFTER handing it
    # over. Cleared when the combined upgrade succeeds.
    identity_upgrade_required = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # Defence-in-depth behind the service-layer balance checks: the DB
            # itself rejects a negative balance, so no bug or race can overdraw.
            models.CheckConstraint(check=models.Q(balance__gte=0), name="wallet_balance_non_negative"),
            # A reserved (virtual) account belongs to exactly one wallet. The
            # funding webhook maps an inbound transfer to a wallet by these, so
            # the DB must guarantee they're unique â€” otherwise a bug or bad data
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


class WemaProvisioningAttempt(models.Model):
    """Server-side binding for the two-step Wema identity/OTP flow.

    Only the keyed identifier hash and last four digits are retained.  The raw
    BVN/NIN is sent to Wema during initiation and then discarded.  OTP validation
    derives the identity type from this row, so a client cannot initiate with one
    identity and mark another one verified after receiving the code.
    """

    BVN = "bvn"
    NIN = "nin"
    IDENTITY_TYPES = [(BVN, "BVN"), (NIN, "NIN")]

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (VERIFIED, VERIFIED), (FAILED, FAILED)]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="wema_provisioning_attempts",
    )
    tracking_id = models.CharField(max_length=160)
    identity_type = models.CharField(max_length=3, choices=IDENTITY_TYPES)
    identity_hash = models.CharField(max_length=64)
    identity_last4 = models.CharField(max_length=4)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    expires_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "tracking_id"],
                name="uniq_user_wema_tracking_id",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "status", "expires_at"],
                         name="wema_attempt_lookup_idx"),
        ]

    @property
    def expired(self) -> bool:
        from django.utils import timezone

        return timezone.now() >= self.expires_at


class Transaction(models.Model):
    """Append-only ledger row. One per money movement."""

    IN = "in"
    OUT = "out"
    DIRECTIONS = [(IN, "Credit"), (OUT, "Debit")]

    PENDING = "Pending"
    SUCCESS = "Successful"
    FAILED = "Failed"
    STATUSES = [(PENDING, PENDING), (SUCCESS, SUCCESS), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transactions")
    service = models.CharField(max_length=80)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    # Currency of `amount`: NGN for the primary wallet, other ISO codes for FX
    # holdings (see CurrencyWallet). Defaults NGN so every existing row is correct.
    currency = models.CharField(max_length=3, default="NGN")
    direction = models.CharField(max_length=3, choices=DIRECTIONS, default=OUT)
    transaction_status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    # Free-form details (meter token, recipient, plan, provider responseâ€¦).
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
            # One ledger row per (user, idempotency_key) when a key is supplied â€”
            # the DB backstop for the dedupe, even under a concurrent race.
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=~models.Q(idempotency_key=""),
                name="uniq_user_idempotency_key",
            ),
        ]

    def save(self, *args, **kwargs):
        """Enforce ledger immutability for identity and money fields.

        Only ``transaction_status`` and ``meta`` may evolve as a payment settles.
        Ownership, reference and the original transaction description are part of
        the audit record just as much as amount/direction/currency; allowing any of
        them to change would let a row be reassigned or disguised after posting.

        (ORM-level guard; a queryset ``.update()`` bypasses ``save()`` â€” back it
        with a Postgres BEFORE UPDATE trigger in production for defence in depth.)
        """
        if self.pk:
            immutable = (
                "user_id", "service", "amount", "currency", "direction",
                "reference", "idempotency_key", "created",
            )
            prior = type(self).objects.filter(pk=self.pk).values(*immutable).first()
            if prior and any(getattr(self, field) != prior[field] for field in immutable):
                raise ValueError(
                    "Ledger rows are immutable: only status and metadata may change once written"
                )
        super().save(*args, **kwargs)

    def __str__(self):
        sign = "+" if self.direction == self.IN else "-"
        return f"{self.service} {sign}₦{self.amount} ({self.transaction_status})"


class ReversalEvidence(models.Model):
    """One durable bank-history row that may represent returned payout money.

    ``Transaction`` remains the money ledger.  This table is the reconciliation
    case ledger: it keeps every provider row independently indexed instead of
    growing an unbounded JSON array on the payout transaction.  It also covers a
    reversal marker that cannot yet be matched to a payout, so the row is claimed
    durably and can be worked by operations rather than living only in logs.

    ``amount`` and the provider identity are the first observed values.  A provider
    later reusing the same reference with a different amount does not overwrite
    them; the competing value is recorded in ``ReversalEvidenceObservation`` and
    the case moves to CONFLICT until two operators explicitly choose an observed
    amount.
    """

    WEMA = "wema"
    PROVIDERS = [(WEMA, "Wema")]

    ACTIVE = "active"
    CONFLICT = "conflict"
    RESOLVED = "resolved"
    STATES = [(ACTIVE, ACTIVE), (CONFLICT, CONFLICT), (RESOLVED, RESOLVED)]

    provider = models.CharField(max_length=20, choices=PROVIDERS, default=WEMA)
    # Keep the readable value for operations, but key idempotency on the full
    # value's hash so an unexpectedly long provider reference cannot overflow a
    # ledger/model column or collide after truncation.
    provider_reference = models.CharField(max_length=255)
    provider_reference_hash = models.CharField(max_length=64)
    ledger_reference = models.CharField(max_length=64, blank=True, default="")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="reversal_evidence",
    )
    payout = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_evidence",
    )
    # Usually this contains the canonical ``payout`` only. A reused provider
    # reference can implicate more than one payout; retaining every association
    # keeps all holds selectable without duplicating the provider event/case.
    associated_payouts = models.ManyToManyField(
        Transaction, blank=True, related_name="reversal_evidence_associations",
    )
    ledger_transaction = models.OneToOneField(
        Transaction, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_evidence_claim",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    initial_reason = models.CharField(max_length=64)
    reason = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=STATES, default=ACTIVE, db_index=True)
    # Incremented only when material evidence changes.  Maker/checker requests bind
    # to it, while harmless repeat sightings merely update ``last_seen``.
    version = models.PositiveIntegerField(default=1)
    resolved_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    resolution_disposition = models.CharField(max_length=48, blank=True, default="")
    resolution_reason = models.CharField(max_length=300, blank=True, default="")
    resolution_approval_id = models.PositiveBigIntegerField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="resolved_reversal_evidence",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_reference_hash"],
                name="uniq_reversal_provider_ref",
            ),
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name="reversal_evidence_amount_positive"),
            models.CheckConstraint(
                check=~models.Q(initial_reason=""),
                name="reversal_initial_reason_present",
            ),
            models.CheckConstraint(
                check=(~models.Q(state="resolved")
                       | (models.Q(resolved_amount__gt=0)
                          & models.Q(resolved_at__isnull=False))),
                name="resolved_reversal_has_amount_time",
            ),
        ]
        indexes = [
            models.Index(fields=["payout", "state"], name="reversal_payout_state_idx"),
            models.Index(fields=["user", "state"], name="reversal_user_state_idx"),
        ]

    @property
    def is_active(self):
        return self.state in (self.ACTIVE, self.CONFLICT)

    def __str__(self):
        payout = self.payout_id or "unmatched"
        return f"{self.provider}:{self.provider_reference} -> {payout} ({self.state})"


class ReversalEvidenceObservation(models.Model):
    """A distinct amount observed for one provider reversal reference."""

    evidence = models.ForeignKey(
        ReversalEvidence, on_delete=models.PROTECT, related_name="observations")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    sightings = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_seen", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["evidence", "amount"], name="uniq_reversal_observed_amount"),
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name="reversal_observation_amount_positive"),
        ]


class ReversalEvidenceResolution(models.Model):
    """Append-only accounting/audit result for a reversal evidence case."""

    evidence = models.ForeignKey(
        ReversalEvidence, on_delete=models.PROTECT, related_name="resolutions")
    payout = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_evidence_resolutions",
    )
    disposition = models.CharField(max_length=48)
    reason = models.CharField(max_length=300, blank=True, default="")
    confirmed_amount = models.DecimalField(max_digits=14, decimal_places=2)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_evidence_resolutions",
    )
    approval_id = models.PositiveBigIntegerField(null=True, blank=True, unique=True)
    movement_transaction = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.PROTECT,
        related_name="reversal_resolutions",
    )
    movement_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True)
    movement_direction = models.CharField(
        max_length=3, choices=Transaction.DIRECTIONS, blank=True, default="")
    payout_status_before = models.CharField(max_length=12, blank=True, default="")
    payout_status_after = models.CharField(max_length=12, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created", "-id"]
        indexes = [
            models.Index(fields=["evidence", "-created"], name="reversal_resolution_idx"),
            models.Index(fields=["payout", "-created"], name="reversal_res_payout_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(confirmed_amount__gt=0),
                name="reversal_resolution_amount_positive",
            ),
            models.CheckConstraint(
                check=(
                    (models.Q(movement_amount__isnull=True)
                     & models.Q(movement_direction=""))
                    | (models.Q(movement_amount__gt=0)
                       & models.Q(movement_direction__in=(Transaction.IN,
                                                          Transaction.OUT)))
                ),
                name="reversal_resolution_movement_valid",
            ),
        ]


class FundingIntent(models.Model):
    """Tracks a wallet top-up from initialize -> verified, keyed by the payment
    reference. Crediting is idempotent: a reference can only fund the wallet once
    (guarded by `credited`), so retries/duplicate webhooks are safe.
    """

    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (PAID, PAID), (FAILED, FAILED)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="funding_intents")
    reference = models.CharField(max_length=64, unique=True, db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    credited = models.BooleanField(default=False)
    # Free-form context, e.g. {"provider": "wema"} â€” records which rail started
    # the charge so verify confirms against the same one.
    meta = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.user} · ₦{self.amount} · {self.status}"


class CurrencyWallet(models.Model):
    """A non-NGN balance the user holds (USD / GBP / CAD â€¦).

    NGN stays in `Wallet` (all existing money code uses it); this table covers FX
    holdings, one row per (user, currency). A DB check keeps balances non-negative.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="currency_wallets")
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
    and a `used` quote can't run again â€” so a stale rate is never settled and a
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


class WemaFaceSession(models.Model):
    """One run of Wema's face-biometric web app.

    ALAT's Account Creation product does liveness in a WEB app, not an API: we send
    the customer to it with their BVN/NIN, they present their face, and the bank
    hands back a `correlationId` proving the check passed. That id — not any image
    and not a client claim — is what verifies the matching BVN/NIN and authorizes
    the without-OTP Tier-1 account-creation call. Tier-2 liveness is a separate
    Prembly-backed flow and never uses this row.

    The row exists to bind the three parties together. Without it the bank's callback
    carries only an identity number, so anyone able to reach the callback URL could
    name someone else's BVN and lift THEIR tier. Instead the app is sent to a
    single-use `state` we minted for one user, and the callback is only honoured when
    the identity it returns hashes to the one that session was opened with.

    Nothing sensitive is retained: the raw BVN/NIN is used to build the URL and then
    dropped, exactly as in WemaProvisioningAttempt, and only its keyed hash is kept.
    """

    BVN = "bvn"
    NIN = "nin"
    IDENTITY_TYPES = [(BVN, "BVN"), (NIN, "NIN")]

    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (VERIFIED, VERIFIED), (FAILED, FAILED)]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="wema_face_sessions",
    )
    # The unguessable handle that appears in the callback/redirect URL. Unique and
    # single-use: a completed session can never be replayed to re-verify.
    state = models.CharField(max_length=64, unique=True)
    identity_type = models.CharField(max_length=3, choices=IDENTITY_TYPES)
    identity_hash = models.CharField(max_length=64)
    # Wema's proof that the face check passed. Kept for audit and dispute handling —
    # it is an opaque reference, not biometric data.
    correlation_id = models.CharField(max_length=160, blank=True, default="")
    # Verification and account issuance are separate outcomes. Legacy sessions
    # stay unknown; a verified identity is NOT evidence that creation was accepted.
    account_state = models.CharField(max_length=24, default="unknown", choices=[
        (value, value) for value in
        ("unknown", "awaiting_callback", "rejected", "review_required")])
    account_failure_category = models.CharField(max_length=48, blank=True, default="")
    account_http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    expires_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "status", "expires_at"],
                         name="wema_face_lookup_idx"),
        ]

    @property
    def expired(self) -> bool:
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"face:{self.user_id}:{self.status}"
