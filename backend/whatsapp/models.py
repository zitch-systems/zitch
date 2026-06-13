"""WhatsApp channel models (slice 1: linking, action state, message log).

Later slices add broadcasts, conversation handover, and admin surfaces; this is
the minimum the webhook + deterministic router need.
"""
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
    link_code = models.CharField(max_length=12, blank=True, default="", db_index=True)
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
    ai_enabled = models.BooleanField(default=True)
    updated = models.DateTimeField(auto_now=True)

    @classmethod
    def for_msisdn(cls, msisdn):
        return cls.objects.get_or_create(msisdn=msisdn)[0]

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

    def __str__(self):
        return f"{self.actor_type}:{self.actor_id} {self.action} {self.target}"


class Broadcast(models.Model):
    """An outbound template campaign to opted-in users (§9)."""

    DRAFT = "draft"
    SENDING = "sending"
    DONE = "done"
    STATUSES = [(DRAFT, DRAFT), (SENDING, SENDING), (DONE, DONE)]

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
    count_queued = models.IntegerField(default=0)
    count_sent = models.IntegerField(default=0)
    count_delivered = models.IntegerField(default=0)
    count_read = models.IntegerField(default=0)
    count_failed = models.IntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]

    def __str__(self):
        return f"{self.template_name} ({self.category}) [{self.status}]"


class BroadcastRecipient(models.Model):
    """One row per recipient of a broadcast, tracked from queued -> read/failed."""

    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name="recipients")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="wa_broadcast_receipts")
    wa_msisdn = models.CharField(max_length=20, db_index=True)
    status = models.CharField(max_length=12, default="queued")  # queued|sent|delivered|read|failed
    wa_message_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    error = models.CharField(max_length=200, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.wa_msisdn} [{self.status}]"
