"""WhatsApp channel models (slice 1: linking, action state, message log).

Later slices add broadcasts, conversation handover, and admin surfaces; this is
the minimum the webhook + deterministic router need.
"""
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class WhatsAppLink(models.Model):
    """Binds a WhatsApp number to a Zitch account.

    Linking is proof-of-control of both sides: the app issues a short-lived
    `link_code` to a signed-in user, who sends it from WhatsApp; the webhook then
    stamps the sender's number onto that row and activates it. One active link
    per number (DB-guarded).
    """

    PENDING = "pending"
    ACTIVE = "active"
    STATUSES = [(PENDING, PENDING), (ACTIVE, ACTIVE)]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="whatsapp_links")
    wa_msisdn = models.CharField(max_length=20, blank=True, default="", db_index=True)
    status = models.CharField(max_length=10, choices=STATUSES, default=PENDING)
    link_code = models.CharField(max_length=64, blank=True, default="", db_index=True)
    # Explicit opt-in: configuring an LLM key must not silently export every
    # customer's free-form chat to a third party.
    # On by default. It began as opt-in consent, but the effect in practice was
    # that every customer got "Sorry, I didn't get that" for natural language
    # while the console reported the AI as live — a consent nobody could discover
    # is not a choice, it is an off switch nobody can find. The customer can
    # still turn it off ("ai off"), and the global kill switch still governs
    # everyone at once.
    ai_enabled = models.BooleanField(default=True)            # per-user AI scope (§8)
    marketing_opt_in = models.BooleanField(default=False)     # broadcasts (§9)
    expires_at = models.DateTimeField(null=True, blank=True)  # link_code TTL
    linked_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            # One active link per WhatsApp number. Pending rows (blank msisdn) are
            # exempt, so several users can have a code outstanding at once.
            models.UniqueConstraint(
                fields=["wa_msisdn"],
                condition=models.Q(status="active") & ~models.Q(wa_msisdn=""),
                name="uniq_active_wa_msisdn",
            ),
        ]

    def __str__(self):
        return f"{self.wa_msisdn or '(unlinked)'} -> {self.user_id} [{self.status}]"


class PendingAction(models.Model):
    """The current multi-turn flow for a number (slot-filling + confirm + PIN).

    At most one unexpired action per number — a new command or `cancel` clears
    it. `payload` accumulates the collected slots; `state` is the next slot the
    router expects.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="wa_pending_actions")
    msisdn = models.CharField(max_length=20, db_index=True)
    action_type = models.CharField(max_length=20)            # transfer | airtime | ...
    state = models.CharField(max_length=20)                  # amount | account | bank | pin | ...
    payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)

    @property
    def expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.msisdn} {self.action_type}/{self.state}"


class WaOnboarding(models.Model):
    """Pre-account onboarding state for a number that is creating a Zitch account
    from WhatsApp (no User exists yet, so PendingAction — which is keyed to a
    user — can't hold it). One row per number; `payload` accumulates the collected
    fields. The PIN is never stored raw: only a hash is held between entry and
    confirmation, and the message log masks the PIN digits in transit."""

    msisdn = models.CharField(max_length=20, unique=True, db_index=True)
    step = models.CharField(max_length=20)                    # first_name | last_name | pin | pin_confirm
    payload = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    created = models.DateTimeField(auto_now_add=True)

    @property
    def expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def __str__(self):
        return f"{self.msisdn} onboarding/{self.step}"


class WaMessageLog(models.Model):
    """Append-only audit of every inbound/outbound message.

    `wa_message_id` (Meta's id) is unique when present, so a re-delivered webhook
    is deduped at the DB. PINs are masked before they ever reach this table.
    """

    IN = "in"
    OUT = "out"
    DIRECTIONS = [(IN, IN), (OUT, OUT)]

    msisdn = models.CharField(max_length=20, db_index=True)
    direction = models.CharField(max_length=3, choices=DIRECTIONS)
    wa_message_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    text = models.TextField(blank=True, default="")
    intent_json = models.JSONField(default=dict, blank=True)  # parsed AI intent (later slices)
    flagged = models.BooleanField(default=False)
    # Durable inbound processing state. A row is no longer synonymous with
    # "processed": if the worker/request dies after claiming it, Meta's retry may
    # reclaim the message after the short lease instead of silently dropping it.
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_attempts = models.PositiveSmallIntegerField(default=0)
    processing_error = models.CharField(max_length=64, blank=True, default="")
    # Only the worker needs the original command text. It is authenticated-
    # encrypted before insertion and erased after successful processing, so PINs,
    # BVNs and free-form chat never appear in a readable database column.
    processing_payload = models.BinaryField(blank=True, default=bytes, editable=False)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            # The operator inbox replays a conversation oldest-first per number.
            models.Index(fields=["msisdn", "created"], name="wamsg_msisdn_created_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["wa_message_id"],
                condition=~models.Q(wa_message_id=""),
                name="uniq_wa_message_id",
            ),
        ]

    def __str__(self):
        return f"{self.direction} {self.msisdn}: {self.text[:40]}"


class SystemSetting(models.Model):
    """Key/value runtime config flippable from the admin — the AI kill switch
    (`ai_enabled_global`), FX margin, etc. Read-through with a default, so a
    missing row is fine."""

    key = models.CharField(max_length=64, unique=True)
    value = models.CharField(max_length=255, blank=True, default="")
    updated = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls, key, default=""):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def get_bool(cls, key, default=False):
        row = cls.objects.filter(key=key).first()
        return (row.value.strip().lower() in {"1", "true", "yes", "on"}) if row else default

    @classmethod
    def set(cls, key, value):
        cls.objects.update_or_create(key=key, defaults={"value": str(value)})

    def __str__(self):
        return f"{self.key}={self.value}"


class ConversationState(models.Model):
    """Per-number conversation control for monitoring + human handover (§10).

    `status=human` pauses the bot (the agent replies); `ai_enabled` is the
    per-conversation AI scope (auto-off during handover)."""

    BOT = "bot"
    HUMAN = "human"
    PAUSED = "paused"
    STATUSES = [(BOT, BOT), (HUMAN, HUMAN), (PAUSED, PAUSED)]

    msisdn = models.CharField(max_length=20, unique=True, db_index=True)
    status = models.CharField(max_length=8, choices=STATUSES, default=BOT)
    assigned_agent = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                       on_delete=models.SET_NULL, related_name="wa_conversations")
    # Conversation-level operations switch. Per-user consent remains on
    # WhatsAppLink.ai_enabled; a new bot conversation may run AI only when that
    # explicit consent and the global kill switch are also on.
    ai_enabled = models.BooleanField(default=True)
    #: When this conversation last proved who it was — a correct PIN in the
    #: encrypted Flow, or a verified biometric through the app hand-off. Read by
    #: the idle re-auth gate; null means "never", which reads as expired.
    #:
    #: WhatsApp's own Chat Lock is the only thing that can gate the chat WINDOW,
    #: and it is user-side and unverifiable, so it can never be a control. What
    #: the bot is willing to REVEAL is ours to gate, and this is how.
    last_verified = models.DateTimeField(null=True, blank=True)
    #: The ledger reference of the transaction this conversation last talked
    #: ABOUT — set when the assistant answers a question about one specific
    #: payment, or sends a receipt for one.
    #:
    #: Without it every message is read in isolation, and a customer who was
    #: just told "your ₦1,000 Electricity — Ikeja was successful" and replies
    #: "Report it" is asked which transaction they mean. They already said. The
    #: word "it" is only meaningful against what was said last, so the channel
    #: has to remember what it said last.
    #:
    #: A reference, not a foreign key: it is what the customer sees on their
    #: receipt, and it survives the row being reversed or re-keyed.
    last_txn_ref = models.CharField(max_length=64, blank=True, default="")
    last_txn_at = models.DateTimeField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)

    #: How long "it" keeps meaning the same payment. Long enough for a customer
    #: to read a receipt and type a reply, short enough that tomorrow's "report
    #: it" is not silently filed against yesterday's transaction.
    REFERENT_TTL = timedelta(minutes=30)

    @classmethod
    def for_msisdn(cls, msisdn):
        return cls.objects.get_or_create(msisdn=msisdn)[0]

    def remember_txn(self, reference: str) -> None:
        """Record which payment this conversation is currently about."""
        ref = (reference or "").strip()[:64]
        if not ref or ref == self.last_txn_ref:
            if ref:
                # Same payment, fresh mention — push the clock out so a
                # follow-up question doesn't fall off the end of the window.
                self.last_txn_at = timezone.now()
                self.save(update_fields=["last_txn_at", "updated"])
            return
        self.last_txn_ref = ref
        self.last_txn_at = timezone.now()
        self.save(update_fields=["last_txn_ref", "last_txn_at", "updated"])

    def referenced_txn(self) -> str:
        """The payment "it" refers to, or "" once the window has passed."""
        if not self.last_txn_ref or self.last_txn_at is None:
            return ""
        if timezone.now() - self.last_txn_at > self.REFERENT_TTL:
            return ""
        return self.last_txn_ref

    def __str__(self):
        return f"{self.msisdn} [{self.status}]"


class AuditLog(models.Model):
    """Append-only record of every admin action / sensitive event (hard-rule #10)."""

    actor_type = models.CharField(max_length=20, default="admin")  # admin | system | user
    actor_id = models.CharField(max_length=64, blank=True, default="")
    action = models.CharField(max_length=80)
    target = models.CharField(max_length=120, blank=True, default="")
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            # The audit screen pages newest-first; recon filters by action prefix.
            models.Index(fields=["-created"], name="audit_created_idx"),
            models.Index(fields=["action"], name="audit_action_idx"),
        ]

    def save(self, *args, **kwargs):
        # Append-only, enforced at the ORM layer: an audit row can be created but
        # never edited. A back-office bug (or a compromised operator) must not be
        # able to rewrite history to cover a fraudulent action.
        if self.pk is not None:
            raise ValueError("AuditLog rows are immutable and cannot be modified")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog rows are immutable and cannot be deleted")

    def __str__(self):
        return f"{self.actor_type}:{self.actor_id} {self.action} {self.target}"


class WebhookEvent(models.Model):
    """Append-only forensic record of every inbound provider callback.

    Lives beside ``AuditLog`` because this app is where the project keeps its
    append-only logs — the app name is historical, not a scope. ``AuditLog`` records
    what WE did; this records what arrived, including the calls we REFUSED, which by
    definition never reach a handler and so leave no other trace but a log line that
    rotates away.

    ``docs/hardening/GAP_ANALYSIS.md`` deferred this as "nice-to-have" because
    redelivery is already idempotent (unique ledger references), which is true and is
    not what this is for. It answers the questions an incident asks and idempotency
    cannot: did the bank ever call at all, how many times, from which IP, with what
    body, and did we accept it. Callback profiling in particular is silent bank-side
    state — the only evidence it took effect is a request arriving.

    Recording is strictly best-effort at every call site: a forensic log must never be
    the reason a callback fails and the bank retries.
    """

    # Verification outcomes. A row exists for a rejected call precisely because a
    # rejected call is the interesting one.
    ACCEPTED = "accepted"
    REJECTED_TOKEN = "rejected_token"
    REJECTED_IP = "rejected_ip"
    REJECTED_SIGNATURE = "rejected_signature"
    REJECTED_METHOD = "rejected_method"
    BAD_BODY = "bad_body"
    OUTCOMES = [(ACCEPTED, ACCEPTED), (REJECTED_TOKEN, REJECTED_TOKEN),
                (REJECTED_IP, REJECTED_IP), (REJECTED_SIGNATURE, REJECTED_SIGNATURE),
                (REJECTED_METHOD, REJECTED_METHOD), (BAD_BODY, BAD_BODY)]

    # Keys redacted from the stored envelope. A forensic log is read by more people,
    # for longer, than any request log — so identity numbers, card data and the
    # secrets that authenticate the call itself never land in it. The envelope keeps
    # its SHAPE (the key survives, the value becomes "[redacted]"), because "the bank
    # sent no BVN" and "we redacted the BVN" must stay distinguishable.
    REDACT_KEYS = frozenset({
        "bvn", "nin", "vnin", "pin", "transactionpin", "otp", "password",
        "securityinfo", "pan", "cardnumber", "cvv", "cvv2", "expirydate",
        "accesstoken", "token", "authorization", "nuban", "accountnumber",
        "email", "phone", "phonenumber", "msisdn", "from", "nubanname", "accountname",
        "customerid", "billerscode", "meter", "meternumber", "smartcard", "iuc",
        # ALAT's face-biometric callback names the identity number plainly as "id"
        # ({success, c_id, id, id_type}), so without this a raw BVN is written into
        # this table in clear — and this table is the one place we keep deliberately
        # immutable, for forensics. A generic key name is exactly how identity data
        # slips past a redaction list built from the field names we expected.
        "id", "identityvalue", "identifiervalue", "bvnornin",
    })

    source = models.CharField(max_length=40)          # wema.account | whatsapp | mono …
    outcome = models.CharField(max_length=24, choices=OUTCOMES, default=ACCEPTED)
    verified = models.BooleanField(default=False)     # did transport auth pass
    http_status = models.PositiveSmallIntegerField(default=200)
    remote_ip = models.CharField(max_length=64, blank=True, default="")
    reference = models.CharField(max_length=120, blank=True, default="")
    action = models.CharField(max_length=80, blank=True, default="")   # what we did as a result
    payload = models.JSONField(default=dict, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["-created"], name="webhook_created_idx"),
            models.Index(fields=["source"], name="webhook_source_idx"),
            # "show me every call carrying this reference" is the question an
            # investigation actually starts from.
            models.Index(fields=["reference"], name="webhook_reference_idx"),
        ]

    @classmethod
    def redact(cls, value):
        """Deep-copy `value` with sensitive leaf values replaced. Matching is on the
        lower-cased key with separators stripped, so nubanName-style camelCase and
        snake_case spellings of the same field are both caught."""
        if isinstance(value, dict):
            out = {}
            for key, inner in value.items():
                flat = str(key).lower().replace("_", "").replace("-", "")
                out[key] = "[redacted]" if flat in cls.REDACT_KEYS else cls.redact(inner)
            return out
        if isinstance(value, list):
            return [cls.redact(v) for v in value]
        return value

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("WebhookEvent rows are immutable and cannot be modified")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("WebhookEvent rows are immutable and cannot be deleted")

    def __str__(self):
        return f"{self.source} {self.outcome} {self.reference}"


class Broadcast(models.Model):
    """An outbound template campaign to opted-in users (§9)."""

    DRAFT = "draft"
    QUEUED = "queued"
    SENDING = "sending"
    DONE = "done"
    STATUSES = [(DRAFT, DRAFT), (QUEUED, QUEUED), (SENDING, SENDING), (DONE, DONE)]

    UTILITY = "utility"
    MARKETING = "marketing"
    AUTHENTICATION = "authentication"
    CATEGORIES = [(UTILITY, UTILITY), (MARKETING, MARKETING), (AUTHENTICATION, AUTHENTICATION)]

    template_name = models.CharField(max_length=120)
    category = models.CharField(max_length=20, choices=CATEGORIES, default=UTILITY)
    body_params = models.JSONField(default=list, blank=True)   # template placeholder values
    segment = models.JSONField(default=dict, blank=True)       # audience query
    status = models.CharField(max_length=10, choices=STATUSES, default=DRAFT)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="wa_broadcasts")
    approval_request = models.OneToOneField(
        "ApprovalRequest", null=True, blank=True, on_delete=models.PROTECT,
        related_name="broadcast",
    )
    count_queued = models.IntegerField(default=0)
    count_sent = models.IntegerField(default=0)
    count_delivered = models.IntegerField(default=0)
    count_read = models.IntegerField(default=0)
    count_failed = models.IntegerField(default=0)
    count_unknown = models.IntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.template_name} ({self.category}) [{self.status}]"


class BroadcastRecipient(models.Model):
    """One row per recipient of a broadcast, tracked from queued -> read/failed."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"
    UNKNOWN = "unknown"

    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="wa_broadcast_receipts")
    wa_msisdn = models.CharField(max_length=20, db_index=True)
    status = models.CharField(max_length=12, default=QUEUED)
    wa_message_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    error = models.CharField(max_length=200, blank=True, default="")
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    processing_attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["broadcast", "wa_msisdn"], name="uniq_broadcast_recipient",
            ),
        ]

    def __str__(self):
        return f"{self.wa_msisdn} [{self.status}]"


class ApprovalRequest(models.Model):
    """A high-risk operator action held for a SECOND operator to approve.

    Maker/checker, deferred in docs/hardening/GAP_ANALYSIS.md on the grounds that
    multi-admin workflows matter once there is a real ops team. The reasoning holds for
    most of the portal and fails for one endpoint: a manual wallet credit creates money
    from nothing. A single compromised or dishonest operator account can mint a balance
    and withdraw it, and every control downstream — tier caps, velocity, the ledger —
    treats that balance as legitimate, because as far as the ledger is concerned it is.

    So the request is stored INSTEAD of executing, and the same payload is executed
    only after a different operator approves it. The action's own code is untouched:
    it runs later through exactly the path it would have taken, which is what keeps
    this from becoming a second, less-tested way to move money.

    Rows are not immutable — a decision has to be recorded on them — but they are
    append-then-decide-once: `decide()` refuses a request that is not pending.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    STATUSES = [(PENDING, PENDING), (APPROVED, APPROVED), (REJECTED, REJECTED),
                (EXECUTED, EXECUTED), (FAILED, FAILED)]

    action = models.CharField(max_length=64)          # e.g. "wallet.credit"
    payload = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=12, choices=STATUSES, default=PENDING)

    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name="approval_requests")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="approval_decisions")
    decision_note = models.CharField(max_length=200, blank=True, default="")
    result = models.JSONField(default=dict, blank=True)

    created = models.DateTimeField(auto_now_add=True)
    decided = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["status", "-created"], name="approval_status_idx"),
        ]

    @property
    def is_pending(self) -> bool:
        return self.status == self.PENDING

    def __str__(self):
        return f"{self.action} {self.status} (by u_{self.requested_by_id})"
