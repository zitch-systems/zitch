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
        constraints = [
            models.UniqueConstraint(
                fields=["wa_message_id"],
                condition=~models.Q(wa_message_id=""),
                name="uniq_wa_message_id",
            ),
        ]

    def __str__(self):
        return f"{self.direction} {self.msisdn}: {self.text[:40]}"
