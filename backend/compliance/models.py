"""Records for the compliance obligations the policy pack already promises.

`compliance/WHATSAPP_PAYMENTS_COMPLIANCE_QA_v1.0.md` is a document given to a regulator.
It makes specific, dated commitments — SARs filed with NFIU within 30 days of detection,
transaction monitoring for ₦5M+ threshold breaches and structuring, records retained 5+
years with an immutable audit trail. Those were policy with nothing in the code behind
them: no case record, no deadline, no way to answer "show me the SARs you filed".

These three models are that missing half. They deliberately record *decisions and
deadlines* rather than trying to automate judgement — an AML case is closed by a human
with a reason, and the value of the record is that the reason and its date exist.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class AmlCase(models.Model):
    """A suspicious-activity case, from detection to an NFIU filing or a dismissal.

    The 30-day clock is the point. The policy commits to filing within 30 days of
    DETECTION, so `detected` is stamped once and never moved, and `due` is derived from
    it — a case that slips is then a queryable fact rather than something reconstructed
    from memory during an examination.
    """

    OPEN = "open"
    INVESTIGATING = "investigating"
    FILED = "filed"          # SAR submitted to NFIU
    DISMISSED = "dismissed"  # reviewed, not suspicious
    STATUSES = [(OPEN, OPEN), (INVESTIGATING, INVESTIGATING), (FILED, FILED),
                (DISMISSED, DISMISSED)]

    # The documented monitoring patterns, so a case says which rule raised it.
    THRESHOLD = "threshold"        # ₦5M+ single movement
    VELOCITY = "velocity"          # rapid succession
    STRUCTURING = "structuring"    # many just under a threshold
    MANUAL = "manual"              # raised by an operator
    TRIGGERS = [(THRESHOLD, THRESHOLD), (VELOCITY, VELOCITY),
                (STRUCTURING, STRUCTURING), (MANUAL, MANUAL)]

    # The regulator-facing deadline, from the policy's own wording.
    SAR_DEADLINE_DAYS = 30

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="aml_cases")
    trigger = models.CharField(max_length=16, choices=TRIGGERS)
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN)
    # What the rule saw: amounts, references, the window. Not free prose — this is what
    # makes a case reproducible.
    evidence = models.JSONField(default=dict, blank=True)
    narrative = models.TextField(blank=True, default="")
    # Set when a SAR is actually submitted. goAML gives a reference; storing it is how
    # "we filed" stops being an assertion.
    nfiu_reference = models.CharField(max_length=64, blank=True, default="")

    detected = models.DateTimeField(default=timezone.now)
    due = models.DateTimeField()
    closed = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="aml_closures")
    # A dedupe handle so a cron re-running does not open the same case twice.
    dedupe_key = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        ordering = ["due", "-detected"]
        indexes = [
            models.Index(fields=["status", "due"], name="aml_status_due_idx"),
            models.Index(fields=["user", "-detected"], name="aml_user_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["dedupe_key"], condition=~models.Q(dedupe_key=""),
                name="uniq_aml_dedupe_key"),
        ]

    def save(self, *args, **kwargs):
        from datetime import timedelta
        if not self.due:
            self.due = self.detected + timedelta(days=self.SAR_DEADLINE_DAYS)
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status in (self.OPEN, self.INVESTIGATING)

    @property
    def overdue(self) -> bool:
        return self.is_open and timezone.now() > self.due

    def __str__(self):
        return f"AML {self.trigger} u_{self.user_id} [{self.status}]"


class DataSubjectRequest(models.Model):
    """An NDPR/GDPR data-subject request, and what was done about it.

    Erasure and AML retention pull in opposite directions, and this is where that is
    resolved explicitly rather than by whichever code ran last. The policy commits to
    retaining transaction records and compliance decisions for 5+ years with an
    immutable audit trail; the data-protection right is to erasure of personal data.
    Both are satisfied by ERASING THE IDENTIFIERS and keeping the financial record
    attached to a pseudonymous id — which is what `compliance.services.erase_user_data`
    does, and why it records exactly which fields it cleared.
    """

    EXPORT = "export"
    ERASE = "erase"
    KINDS = [(EXPORT, EXPORT), (ERASE, ERASE)]

    RECEIVED = "received"
    COMPLETED = "completed"
    REFUSED = "refused"
    STATUSES = [(RECEIVED, RECEIVED), (COMPLETED, COMPLETED), (REFUSED, REFUSED)]

    # NDPR's response window for a data-subject request.
    RESPONSE_DAYS = 30

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="data_requests")
    # Kept separately because the point of an erasure is that `user` stops identifying
    # anybody — after one, this is the only handle left on the request.
    subject_label = models.CharField(max_length=120, blank=True, default="")
    kind = models.CharField(max_length=8, choices=KINDS)
    status = models.CharField(max_length=10, choices=STATUSES, default=RECEIVED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL,
                                     related_name="data_requests_handled")
    # For an erasure: which models/fields were cleared and which were retained, with the
    # reason. This is the artifact an examiner asks for.
    outcome = models.JSONField(default=dict, blank=True)
    note = models.CharField(max_length=300, blank=True, default="")

    received = models.DateTimeField(default=timezone.now)
    due = models.DateTimeField()
    completed = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received"]
        indexes = [models.Index(fields=["status", "due"], name="dsr_status_due_idx")]

    def save(self, *args, **kwargs):
        from datetime import timedelta
        if not self.due:
            self.due = self.received + timedelta(days=self.RESPONSE_DAYS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"DSR {self.kind} {self.subject_label} [{self.status}]"


class Dispute(models.Model):
    """A customer's challenge to a transaction.

    Raised against a ledger reference rather than a Transaction FK on purpose: a customer
    disputes "the ₦5,000 that left on Tuesday", and the reference is the string that
    appears on their receipt, in support tooling and in the bank's records. It also means
    a dispute survives if the row is later reversed.

    A dispute never moves money by itself. The remedy is an existing, audited path — a
    refund or a manual credit, which above the ceiling now needs a second approver.
    Wiring an automatic refund to a customer-raised claim would be a free-money button.
    """

    OPEN = "open"
    INVESTIGATING = "investigating"
    UPHELD = "upheld"          # customer was right; remedy applied separately
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"
    STATUSES = [(OPEN, OPEN), (INVESTIGATING, INVESTIGATING), (UPHELD, UPHELD),
                (DECLINED, DECLINED), (WITHDRAWN, WITHDRAWN)]

    # Reasons a customer can actually pick, kept short so the list is usable on a phone.
    NOT_RECEIVED = "not_received"
    WRONG_AMOUNT = "wrong_amount"
    UNAUTHORISED = "unauthorised"
    DUPLICATE = "duplicate"
    OTHER = "other"
    REASONS = [(NOT_RECEIVED, NOT_RECEIVED), (WRONG_AMOUNT, WRONG_AMOUNT),
               (UNAUTHORISED, UNAUTHORISED), (DUPLICATE, DUPLICATE), (OTHER, OTHER)]

    RESPONSE_DAYS = 14

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name="disputes")
    reference = models.CharField(max_length=64)
    reason = models.CharField(max_length=16, choices=REASONS)
    detail = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUSES, default=OPEN)
    resolution = models.CharField(max_length=500, blank=True, default="")
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="dispute_decisions")

    # default=, not auto_now_add=: auto_now_add is applied during the INSERT, i.e. after
    # save() has already derived `due` from it, which left the deadline a second short of
    # the documented window. The other two models here use the same pattern.
    created = models.DateTimeField(default=timezone.now)
    due = models.DateTimeField()
    resolved = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["status", "due"], name="dispute_status_due_idx"),
            models.Index(fields=["reference"], name="dispute_reference_idx"),
        ]
        constraints = [
            # One open dispute per customer per reference. Without this, a frustrated
            # customer tapping twice creates two cases that get worked separately and
            # can be resolved inconsistently.
            models.UniqueConstraint(
                fields=["user", "reference"],
                condition=models.Q(status__in=["open", "investigating"]),
                name="uniq_open_dispute_per_reference"),
        ]

    def save(self, *args, **kwargs):
        from datetime import timedelta
        if not self.due:
            self.due = self.created + timedelta(days=self.RESPONSE_DAYS)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Dispute {self.reference} u_{self.user_id} [{self.status}]"
