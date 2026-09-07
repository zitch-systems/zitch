from django.conf import settings
from django.db import models


class Bank(models.Model):
    """A payout bank the user can send to."""

    code = models.CharField(max_length=20, unique=True)   # e.g. "gtb"
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=9, blank=True, default="")
    # NIBSS / payout-provider bank code, used when live.
    bank_code = models.CharField(max_length=10, blank=True, default="")
    # Hosted logo image for the app's bank picker (blank -> the app shows a
    # colored monogram). A URL rather than a bundled asset so logos can be
    # updated/added server-side without shipping an app release.
    logo = models.URLField(max_length=300, blank=True, default="")
    # High-volume banks probed by the auto-detect name-enquiry sweep
    # (detect_account_banks). Every active bank is manually pickable; only the
    # popular set is swept, so a typed account never fans out ~40 paid
    # name-enquiry calls.
    popular = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Beneficiary(models.Model):
    """A transfer recipient. Auto-created on first transfer; deduped per user by
    (account_number, bank_name).

    Every row is a RECENT: an account this customer has actually paid. That is
    what the send screen leans on when it fills in a bank and holder name from a
    typed account number without a fresh name enquiry — the row is evidence that
    money once went there and arrived.

    `saved` is the customer's own decision layered on top: it turns True only
    when they explicitly say "keep this one", which is what puts the recipient in
    their address book and lets them be paid by name later. Nothing in the payout
    path ever sets it, so a row's provenance stays readable.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="beneficiaries")
    name = models.CharField(max_length=80)
    account_number = models.CharField(max_length=20)
    bank_name = models.CharField(max_length=60)
    bank_code = models.CharField(max_length=20, blank=True, default="")
    color = models.CharField(max_length=9, blank=True, default="#0FA295")
    # The customer's own label for this recipient ("Mum", "landlord"). Display
    # only: `name` stays the holder name the bank returned, because that is the
    # value bank_transfer re-confirms against a fresh name enquiry before money
    # moves. A nickname sitting in `name` would read as a mismatch and block a
    # perfectly good transfer.
    nickname = models.CharField(max_length=80, blank=True, default="")
    # False on a row we wrote ourselves after a payout; True only once the
    # customer has said to keep it.
    saved = models.BooleanField(default=False)
    # Number of completed or accepted transfers to this exact account/bank.
    # Automatic recipient memory is private until the third transfer; at 51 it
    # is promoted to the user's explicit Beneficiary address book.
    transfer_count = models.PositiveIntegerField(default=0)
    # WhatsApp asks about automatic saving at most once per recipient.
    save_offer_sent = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created"]
        unique_together = [("user", "account_number", "bank_name")]

    @property
    def initials(self) -> str:
        # Deliberately off `name`, not `display_name`. App builds already on
        # customers' phones render this disc beside the holder name, so deriving
        # it from a nickname would show "MU" next to "JOHN DOE" on every handset
        # that has not updated — which reads as a bug rather than a nickname.
        return "".join(w[0] for w in self.name.split()[:2]).upper() or "ZT"

    @property
    def display_name(self) -> str:
        """What to call this recipient on screen: the customer's own label when
        they set one, otherwise the holder name the bank gave us."""
        return self.nickname or self.name

    def __str__(self):
        return f"{self.name} · {self.account_number} ({self.bank_name})"
