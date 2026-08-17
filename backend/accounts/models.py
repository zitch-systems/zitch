import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

log = logging.getLogger("zitch.security")


def transaction_pin_rejection(pin: str) -> str:
    """Return a customer-safe reason when a newly chosen PIN is unacceptable.

    This policy lives server-side because the mobile warning is only a usability
    aid: an attacker can call the API directly, and WhatsApp creates the same PIN
    through a separate encrypted Flow. Existing legacy PIN hashes remain
    verifiable; the rule applies only when a customer chooses a new PIN.
    """
    if not re.fullmatch(r"\d{6}", pin or ""):
        return "Your PIN must be exactly 6 digits"

    # Block repeated blocks (000000, 121212, 123123) and cyclic straight runs
    # (123456, 654321, 789012). These are among the first guesses made against a
    # short numeric secret and defeat much of the value of the attempt lockout.
    for width in range(1, len(pin)):
        if len(pin) % width == 0 and pin[:width] * (len(pin) // width) == pin:
            return "Choose a less predictable PIN; repeated or sequential digits are not allowed"
    digits = [int(char) for char in pin]
    ascending = all((digits[index] - digits[index - 1]) % 10 == 1
                    for index in range(1, len(digits)))
    descending = all((digits[index - 1] - digits[index]) % 10 == 1
                     for index in range(1, len(digits)))
    if ascending or descending:
        return "Choose a less predictable PIN; repeated or sequential digits are not allowed"
    return ""


def hash_identifier(value: str) -> str:
    """Keyed HMAC-SHA256 of a sensitive government ID (BVN/NIN). Keyed with a
    secret that is not in the database, so the small 11-digit space can't be
    brute-forced from a DB leak the way a plain SHA-256 could. Deterministic, so
    it still supports audit / duplicate-detection.

    The key is KYC_HASH_KEY, which DEFAULTS to SECRET_KEY for backward
    compatibility (existing hashes — including those written by migration 0009 —
    keep verifying). Set KYC_HASH_KEY explicitly and pin it: unlike SECRET_KEY
    (which may be rotated), it must NEVER change, or every stored BVN/NIN hash
    becomes unverifiable since the raw value is not retained."""
    if not value:
        return ""
    key = getattr(settings, "KYC_HASH_KEY", "") or settings.SECRET_KEY
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


class User(AbstractUser):
    """Custom user keyed by phone, with a hashed transaction PIN.

    `username` is kept (Django requires it) but we authenticate by phone/email.
    """

    # KYC tiers (CBN-style; adjust to your licence). Tier requirements ascend:
    #   Tier 0 — Unverified: email + phone only (sign-up).
    #   Tier 1 — Verified:   + BVN AND NIN verified.
    #   Tier 2 — Enhanced:   + facial (liveness) AND residential-address verified.
    #   Tier 3 — Premium:    + a government-issued ID document verified.
    # See recompute_tier(). The per-tier caps below live on the user, so they apply
    # identically in the app and on WhatsApp.
    TIER_LIMITS = {0: Decimal("20000"), 1: Decimal("50000"),
                   2: Decimal("200000"), 3: Decimal("5000000")}
    # Per-day aggregate caps by tier, on top of the per-transaction limit.
    DAILY_TRANSFER_LIMITS = {0: Decimal("20000"), 1: Decimal("50000"),
                             2: Decimal("1000000"), 3: Decimal("5000000")}
    DAILY_BILL_LIMITS = {0: Decimal("10000"), 1: Decimal("20000"),
                         2: Decimal("100000"), 3: Decimal("500000")}
    # Single transfers at/above this require step-up (face) verification, which is
    # a Tier 2 attribute — so a Tier 0/1 customer cannot move >= this in one go.
    LARGE_TXN_THRESHOLD = Decimal("100000")

    # Transaction-PIN brute-force policy: after this many wrong PINs in a row,
    # lock further attempts. A stolen session token then can't be used to guess
    # the short PIN (10k combos) that gates money movement.
    #
    # The lockout ESCALATES, because one round of five wrong guesses reads very
    # differently from two. The first is what a real customer does when they have
    # forgotten which PIN they set — an hour is enough to break an accidental
    # loop without stranding them. Sitting out that hour and immediately burning
    # five more is not forgetfulness; it is someone working through a keyspace,
    # so the second lock is a day. That also changes the arithmetic for an
    # attacker: 10k combinations at 5 per day is ~5 years, where at 5 per hour it
    # would be ~three months.
    #
    # Escalation is capped at the 24-hour tier rather than growing without bound.
    # An unbounded ladder mostly punishes the locked-out customer, and the real
    # way out of a repeat lock is a PIN reset (verified identity + a live SMS
    # code), which is deliberately NOT gated on the lock — see _start_pin_reset.
    PIN_MAX_ATTEMPTS = 5
    PIN_LOCKOUT_MINUTES = 60                    # first lock: one hour
    PIN_LOCKOUT_ESCALATED_MINUTES = 24 * 60     # locked again without a correct PIN since

    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    #: A limit the CUSTOMER chose for themselves, at or below their tier ceiling.
    #: NULL means "no self-limit" and the tier ceiling applies — deliberately
    #: distinct from 0, which is a customer who has frozen their own spending.
    #: Only ever tightens `transaction_limit`; see that property.
    self_txn_limit = models.DecimalField(max_digits=14, decimal_places=2,
                                        null=True, blank=True)
    transaction_pin = models.CharField(max_length=128, blank=True, default="")
    pin_failed_attempts = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)
    #: Consecutive lockouts with no correct PIN in between — the escalation
    #: counter, not a lifetime tally. Cleared by a correct PIN, by setting a new
    #: PIN, and by an operator unlock, so a customer who locks themselves out
    #: once and then banks normally for months starts from the one-hour tier
    #: again rather than inheriting a day-long lock from last year.
    pin_lockout_strikes = models.PositiveSmallIntegerField(default=0)

    # --- KYC ---
    tier = models.PositiveSmallIntegerField(default=0)
    # BVN/NIN are never read back by the app (the provider re-checks the value at
    # submit time; only the *_verified flags are ever returned), so the raw number
    # is NOT kept at rest — a DB leak then can't expose it. Instead store a keyed
    # HMAC (for audit / duplicate-detection) plus the last 4 digits for support.
    bvn_hash = models.CharField(max_length=64, blank=True, default="")
    bvn_last4 = models.CharField(max_length=4, blank=True, default="")
    bvn_verified = models.BooleanField(default=False)
    nin_hash = models.CharField(max_length=64, blank=True, default="")
    nin_last4 = models.CharField(max_length=4, blank=True, default="")
    nin_verified = models.BooleanField(default=False)
    face_verified = models.BooleanField(default=False)
    # Tier 2: a verified residential address. We keep the (non-sensitive) address
    # string for support/records and a verified flag the tier logic reads.
    address = models.CharField(max_length=255, blank=True, default="")
    address_verified = models.BooleanField(default=False)
    # Chat-onboarded accounts carry credentials collected over WhatsApp: name,
    # email and PIN, but never a password and never a verified contact channel.
    # The flag gates the app-side upgrade: KYC (and therefore any tier above the
    # unverified floor) stays closed until the email collected in chat is
    # re-verified in the app; the phone re-verifies itself because these accounts
    # have no usable password, so entering the app at all runs the OTP-backed
    # password-reset flow against the same number.
    onboarded_via_whatsapp = models.BooleanField(default=False)
    # Proof the customer controls the SIM, not merely a signed-in messenger.
    # App signup sets it (the signup OTP is exactly this proof). A WhatsApp
    # signup does NOT: a WhatsApp session survives a SIM swap, so possession of
    # the chat is weaker than possession of the number, and the chat KYC flow
    # runs its own SMS round-trip to earn it. Existing accounts are backfilled
    # True by the migration — every one of them came through the app OTP.
    phone_verified = models.BooleanField(default=False)
    # Set by the in-app email OTP round-trip. Deliberately consulted by recovery:
    # an UNVERIFIED chat-collected email is one typo away from being someone
    # else's inbox, so it must never receive a password-reset code.
    email_verified = models.BooleanField(default=False)
    # Tier 3: a verified government-issued ID document (passport / driver's licence
    # / voter's card / NIN slip). Only the verified flag and the document type are
    # retained — never the raw document image.
    id_document_type = models.CharField(max_length=32, blank=True, default="")
    id_document_verified = models.BooleanField(default=False)

    # Profile photo: storage-relative path (e.g. "avatars/3-ab12.png"); resolved
    # to an absolute URL via MEDIA_URL when returned to the app.
    avatar = models.CharField(max_length=255, blank=True, default="")

    #: True for accounts still holding a 4-digit PIN from before the 6-digit
    #: rule. They may still sign in and receive money; they must set a new PIN
    #: before money can leave. Set by data migration, cleared by
    #: set_transaction_pin.
    pin_reset_required = models.BooleanField(default=False)

    #: Transaction PINs are 6 digits. Enforced at the entry points rather than
    #: here, so existing 4-digit hashes stay verifiable — an account keeps
    #: working right up until it is asked to set a new one.
    PIN_LENGTH = 6

    #: Everything set_transaction_pin touches. Callers that save with
    #: update_fields MUST splat this, or the parts they omit are silently
    #: dropped — which is exactly how a WhatsApp PIN reset used to leave the old
    #: PIN's lockout in place, so the customer set a new PIN and still could not
    #: spend. Naming the set once is what stops the surfaces drifting again.
    PIN_UPDATE_FIELDS = ("transaction_pin", "pin_reset_required",
                         "pin_failed_attempts", "pin_locked_until",
                         "pin_lockout_strikes")

    def set_transaction_pin(self, raw_pin: str) -> None:
        self.transaction_pin = make_password(raw_pin)
        # Setting a PIN is what clears the reset demand, whichever surface set
        # it. The flag is the only thing that knows a hash is a short old one:
        # a hash cannot be measured, so the length has to be recorded when it
        # changes rather than inferred later.
        self.pin_reset_required = False
        # A lockout is brute-force state about the PIN being REPLACED, so it
        # cannot outlive it. Every surface that sets a PIN has already proved
        # identity by a route the lock does not gate (the app's password, or the
        # chat's verified-identity + live SMS code), so carrying the lock over
        # would only punish the customer who just did the recovery we told them
        # to do — and on the 24-hour tier that is a day of being unable to pay
        # with a PIN they now know.
        self.pin_failed_attempts = 0
        self.pin_locked_until = None
        self.pin_lockout_strikes = 0

    def check_transaction_pin(self, raw_pin: str) -> bool:
        if not self.transaction_pin:
            return False
        return check_password(raw_pin, self.transaction_pin)

    def set_bvn(self, raw: str) -> None:
        """Store only a keyed hash + the last 4 of the BVN — never the raw number."""
        self.bvn_hash = hash_identifier(raw)
        self.bvn_last4 = (raw or "")[-4:]

    def set_nin(self, raw: str) -> None:
        """Store only a keyed hash + the last 4 of the NIN — never the raw number."""
        self.nin_hash = hash_identifier(raw)
        self.nin_last4 = (raw or "")[-4:]

    @property
    def pin_locked(self) -> bool:
        """True while the transaction PIN is temporarily locked after too many
        wrong attempts (see PIN_MAX_ATTEMPTS / PIN_LOCKOUT_MINUTES)."""
        return self.pin_locked_until is not None and timezone.now() < self.pin_locked_until

    @property
    def pin_lock_is_escalated(self) -> bool:
        """True when the CURRENT lock is a repeat one (the 24-hour tier). Surfaces
        offer the PIN reset here: an hour is worth waiting out, a day is not, and
        a customer on their second lock is the one who most likely cannot
        remember the PIN at all."""
        return self.pin_locked and self.pin_lockout_strikes >= 2

    @property
    def tier_transaction_limit(self) -> Decimal:
        """The ceiling KYC allows at this tier. The compliance bound, and the one
        a customer can never raise."""
        return self.TIER_LIMITS.get(self.tier, self.TIER_LIMITS[0])

    @property
    def transaction_limit(self) -> Decimal:
        """What this account may actually move in one transaction.

        The tier ceiling, unless the customer has set themselves a LOWER one. The
        min() is the whole point and is deliberately not a max(): a self-imposed
        limit is a guardrail against a bad day or a stolen session, so it may only
        ever tighten what KYC already allows. Raising a limit is earned by
        verifying identity, never by asking for it in settings.

        min() also means a stale self-limit can never become a loophole in the
        other direction — a customer who set 500,000 at Tier 3 and later dropped
        to Tier 1 is bounded by Tier 1, not by the number they once chose.
        """
        ceiling = self.tier_transaction_limit
        chosen = self.self_txn_limit
        return min(ceiling, chosen) if chosen is not None else ceiling

    @property
    def daily_transfer_limit(self) -> Decimal:
        return self.DAILY_TRANSFER_LIMITS.get(self.tier, self.DAILY_TRANSFER_LIMITS[0])

    @property
    def daily_bill_limit(self) -> Decimal:
        return self.DAILY_BILL_LIMITS.get(self.tier, self.DAILY_BILL_LIMITS[0])

    def recompute_tier(self) -> None:
        """Derive the KYC tier from the verifications completed (ascending):
        Tier 1 needs a verified EMAIL and PHONE plus BVN AND NIN; Tier 2 adds
        face AND address; Tier 3 adds a verified government ID document. Anything
        less is Tier 0 (unverified).

        Both contact requirements apply to every account, however it signed up.
        The app earns `phone_verified` at signup (that IS the signup OTP); a
        WhatsApp signup earns it in the chat KYC flow, because a messenger
        session outlives a SIM swap and so is not by itself proof of the
        number."""
        # Both contact channels must be proven before any tier above the floor.
        if not (self.email_verified and self.phone_verified):
            self.tier = 0
        elif (self.bvn_verified and self.nin_verified and self.face_verified
                and self.address_verified and self.id_document_verified):
            self.tier = 3
        elif (self.bvn_verified and self.nin_verified and self.face_verified
                and self.address_verified):
            self.tier = 2
        elif self.bvn_verified and self.nin_verified:
            self.tier = 1
        else:
            self.tier = 0

    def set_address(self, raw: str) -> None:
        self.address = (raw or "").strip()[:255]

    class Meta(AbstractUser.Meta):
        indexes = [
            # sign-in matches email case-insensitively (Q(email__iexact=...)); a
            # functional LOWER(email) index turns that branch from a full user-table
            # scan into an index lookup — sign-in is both hot and a brute-force
            # target, so the scan is a DoS amplifier as the user table grows.
            models.Index(Lower("email"), name="user_email_lower_idx"),
        ]
        constraints = [
            # Wema rejects reused BVNs/NINs.  Enforce the same ownership boundary
            # locally (scoped to non-empty hashes so unverified users coexist).
            models.UniqueConstraint(
                fields=["bvn_hash"], condition=~models.Q(bvn_hash=""),
                name="uniq_user_bvn_hash",
            ),
            models.UniqueConstraint(
                fields=["nin_hash"], condition=~models.Q(nin_hash=""),
                name="uniq_user_nin_hash",
            ),
        ]

    def __str__(self):
        return self.phone or self.email or self.username


class AccessToken(models.Model):
    """Opaque bearer token for the app's `access_token`.

    Only the SHA-256 hash of the token is stored — never the token itself — so a
    database leak (backup, SQLi, stray query log) can't be replayed as a live
    session, the same reason passwords are hashed. The raw token exists only in
    the issuing response and the client's keychain. SHA-256 (not a slow KDF) is
    correct here: the token is 256 bits of CSPRNG output, so there is nothing to
    brute-force.
    """

    # A token is minted for exactly one surface. An `app` token drives the mobile
    # app; an `admin` token drives the staff back-office. They are NOT
    # interchangeable: a stolen app token must never reach an admin endpoint, and
    # admin tokens expire far sooner (ADMIN_TOKEN_TTL_HOURS). `resolve` enforces
    # the split, so scope is a hard boundary, not a hint.
    APP = "app"
    ADMIN = "admin"
    SCOPES = [(APP, APP), (ADMIN, ADMIN)]

    key = models.CharField(max_length=64, unique=True, db_index=True)  # sha256 hex of the token
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tokens")
    scope = models.CharField(max_length=8, choices=SCOPES, default=APP, db_index=True)
    # New mobile sessions are bound to the per-install keychain identifier sent on
    # the request that authenticated them.  Existing rows remain unbound so this can
    # deploy without signing every tester out; every newly issued app token is bound.
    # The identifier is not a hardware ID or a secret, but requiring the same value
    # stops a bearer copied out of logs/storage from working on a different client.
    device_id = models.CharField(max_length=64, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256((raw or "").encode()).hexdigest()

    @classmethod
    def issue(cls, user, scope: str = APP, device_id: str = "") -> "AccessToken":
        """Create a session token. The DB stores only the hash; the returned
        instance carries the RAW token on `.key` (in memory, unsaved) for the
        caller to hand to the client — do not re-save the instance afterwards.

        `scope` binds the token to a surface (app vs admin) — mint admin tokens
        with `scope=AccessToken.ADMIN` so they resolve only on staff endpoints
        and inherit the shorter admin TTL."""
        raw = secrets.token_hex(32)
        bound_device = (device_id or "").strip()[:64] if scope == cls.APP else ""
        tok = cls.objects.create(key=cls._hash(raw), user=user, scope=scope,
                                 device_id=bound_device)
        tok.key = raw  # transient: expose the raw token to the caller without persisting it
        return tok

    @classmethod
    def resolve(cls, key: str, required_scope: str | None = None,
                device_id: str | None = None):
        """Resolve a raw token to its user, or None if it is unknown, expired,
        out of scope, or belongs to a deactivated account.

        `required_scope` (when given) rejects a token minted for a different
        surface — an app token presented to an admin endpoint resolves to None
        even though the row is otherwise valid. Admin-scoped tokens expire after
        ADMIN_TOKEN_TTL_HOURS; app tokens after TOKEN_TTL_HOURS."""
        if not key:
            return None
        try:
            tok = cls.objects.select_related("user").get(key=cls._hash(key))
        except cls.DoesNotExist:
            return None
        if required_scope is not None and tok.scope != required_scope:
            return None
        # `None` is the trusted non-HTTP compatibility path used by internal code and
        # tests. HTTP always supplies a string (possibly empty), so stripping the
        # header cannot bypass a binding.
        if tok.device_id and device_id is not None:
            presented_device = (device_id or "").strip()[:64]
            if not hmac.compare_digest(tok.device_id, presented_device):
                log.warning("session_device_mismatch user=%s token=%s", tok.user_id,
                            tok.pk)
                return None
        if tok.scope == cls.ADMIN:
            ttl_hours = getattr(settings, "ADMIN_TOKEN_TTL_HOURS", 2)
        else:
            ttl_hours = settings.TOKEN_TTL_HOURS
        if timezone.now() - tok.created > timedelta(hours=ttl_hours):
            tok.delete()
            return None
        # A frozen / deactivated account must lose every live session
        # immediately — freezing a user revokes their tokens, but a token that
        # slips through (race, cached lookup) is still refused here.
        if not tok.user.is_active:
            return None
        return tok.user

    def __str__(self):
        return f"{self.user} · {self.key[:8]}…"


class RefreshToken(models.Model):
    """The long-lived half of a mobile session, used only to mint access tokens.

    The problem it solves: TOKEN_TTL_HOURS is 24, so the app signed a customer
    out once a day and biometric sign-in fell back to email+password. The two
    ways to stop that are raising the access-token window or adding this. Raising
    the window is strictly worse — an access token travels on every single API
    call, so a longer life multiplies the value of one intercepted or
    log-scraped bearer, and there is no way to end it early short of deleting
    the row. A refresh token is presented to exactly ONE endpoint, rotates on
    every use, and can be revoked as a family. So the access token gets SHORTER,
    not longer, and this carries the session.

    Three properties do the security work:

    * **Rotation.** Each refresh mints a new refresh token and burns the old
      one. A stolen token is therefore only useful until the real client next
      refreshes.
    * **Reuse detection.** Because rotation burns the old token, a SECOND
      presentation of one is proof that two parties hold it — the definition of
      a theft. That revokes the whole family, so the attacker and the victim are
      both signed out and the victim notices, which is the outcome to want.
      Silently issuing a fresh token to whoever asked would hide the breach.
    * **An absolute ceiling.** Rotation alone lets a family live forever. The
      family expires REFRESH_ABSOLUTE_DAYS after the password was last proven,
      so a session cannot outlive the credential behind it indefinitely.

    Only the SHA-256 hash is stored, for the same reason as AccessToken: 256 bits
    of CSPRNG output has nothing to brute-force, and a leaked backup must not be
    replayable.
    """

    key = models.CharField(max_length=64, unique=True, db_index=True)  # sha256 hex
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="refresh_tokens")
    # Every token minted from one sign-in shares a family id, so reuse detection
    # can revoke the whole chain rather than the single presented row — which
    # would leave the thief's newer token working.
    family = models.CharField(max_length=32, db_index=True)
    # Bound to the install that signed in, exactly as the access token is: a
    # refresh token lifted from storage or a log must not work on another client.
    device_id = models.CharField(max_length=64, blank=True, default="")
    created = models.DateTimeField(auto_now_add=True)
    # When the family began — carried forward across rotations so the absolute
    # ceiling measures from the sign-in, not from the last refresh.
    family_started = models.DateTimeField(default=timezone.now)
    # Set when this row is spent. A spent row is KEPT (not deleted) because its
    # existence is what makes a replay detectable.
    used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "family"])]

    #: Why a refresh was refused. The endpoint maps all of them to one 401
    #: message — telling a caller WHICH of these it hit is a free oracle — but
    #: they are distinguished here so the logs and the tests can tell a routine
    #: expiry from a live token theft.
    UNKNOWN = "unknown"
    REUSED = "reused"
    REVOKED = "revoked"
    EXPIRED = "expired"
    DEVICE = "device_mismatch"
    INACTIVE = "inactive"

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256((raw or "").encode()).hexdigest()

    @classmethod
    def _idle_ttl(cls) -> timedelta:
        return timedelta(days=int(getattr(settings, "REFRESH_TOKEN_TTL_DAYS", 30)))

    @classmethod
    def _absolute_ttl(cls) -> timedelta:
        return timedelta(days=int(getattr(settings, "REFRESH_ABSOLUTE_DAYS", 90)))

    @classmethod
    def issue(cls, user, device_id: str = "", family: str = "",
              family_started=None) -> "RefreshToken":
        """Mint a refresh token. As with AccessToken.issue, the returned instance
        carries the RAW token on `.key` in memory and the DB holds only its hash,
        so the instance must not be re-saved."""
        raw = secrets.token_hex(32)
        tok = cls.objects.create(
            key=cls._hash(raw), user=user,
            family=family or secrets.token_hex(16),
            device_id=(device_id or "").strip()[:64],
            family_started=family_started or timezone.now())
        tok.key = raw  # transient
        return tok

    @classmethod
    def revoke_family(cls, user_id: int, family: str) -> int:
        """Kill every token in one sign-in's chain. Returns the number closed."""
        return cls.objects.filter(user_id=user_id, family=family,
                                  revoked_at__isnull=True).update(
            revoked_at=timezone.now())

    @classmethod
    def rotate(cls, raw: str, device_id: str | None = None):
        """Spend `raw` and return (new_refresh_token, user), or (None, reason).

        Row-locked and single-statement-guarded: two requests racing with the
        same token must not both succeed, because "two holders" is precisely the
        signal reuse detection exists to catch. The winning UPDATE is conditional
        on used_at still being NULL, so the loser is treated as a replay.
        """
        from django.db import transaction as db_transaction

        if not raw:
            return None, cls.UNKNOWN
        with db_transaction.atomic():
            tok = (cls.objects.select_for_update()
                   .select_related("user")
                   .filter(key=cls._hash(raw)).first())
            if tok is None:
                return None, cls.UNKNOWN
            now = timezone.now()
            if tok.used_at is not None:
                # A second presentation of a spent token: the real client and
                # someone else both hold this chain. End the whole family.
                revoked = cls.revoke_family(tok.user_id, tok.family)
                log.warning("refresh_token_reuse user=%s family=%s revoked=%s",
                            tok.user_id, tok.family, revoked)
                return None, cls.REUSED
            if tok.revoked_at is not None:
                return None, cls.REVOKED
            if (now - tok.created > cls._idle_ttl()
                    or now - tok.family_started > cls._absolute_ttl()):
                return None, cls.EXPIRED
            # Same binding rule as the access token, including the `None`
            # compatibility path for internal callers and tests. HTTP always
            # passes a string, so stripping the header cannot bypass a binding.
            if tok.device_id and device_id is not None:
                if not hmac.compare_digest(tok.device_id,
                                           (device_id or "").strip()[:64]):
                    log.warning("refresh_device_mismatch user=%s family=%s",
                                tok.user_id, tok.family)
                    return None, cls.DEVICE
            if not tok.user.is_active:
                return None, cls.INACTIVE
            # Conditional burn — the loser of a race sees 0 rows updated and is
            # sent back through the reuse path on its next attempt.
            burned = cls.objects.filter(pk=tok.pk, used_at__isnull=True).update(used_at=now)
            if not burned:
                return None, cls.REUSED
            fresh = cls.issue(tok.user, device_id=tok.device_id,
                              family=tok.family, family_started=tok.family_started)
        return fresh, tok.user

    def __str__(self):
        return f"{self.user} · {self.family[:8]}…"


class OTP(models.Model):
    """One-time code sent during phone verification or account recovery.

    `purpose` keeps a recovery code from being accepted by the signup verifier
    (which would mint a token for an existing account without a password) and
    vice-versa — each verifier filters to its own purpose.
    """

    SIGNUP = "signup"
    RESET = "reset"
    EMAIL = "email"           # in-app verification of a chat-collected email
    PURPOSES = [(SIGNUP, SIGNUP), (RESET, RESET), (EMAIL, EMAIL)]

    phone = models.CharField(max_length=20, db_index=True)
    # The raw 6-digit code is NEVER stored — only its keyed hash. A 6-digit code
    # has just 10^6 possibilities, so a plain SHA-256 would be trivially reversed
    # from a DB leak; HMAC with a server-side secret means an attacker needs the
    # secret too. Without this, a leaked backup / read replica / SQL-injection
    # would hand out live signup codes (which authenticate straight into the
    # matching account) and reset codes (which reset the password).
    code_hash = models.CharField(max_length=64)
    email = models.EmailField(blank=True, default="")
    purpose = models.CharField(max_length=10, choices=PURPOSES, default=SIGNUP)
    created = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    EXPIRY_MINUTES = 10
    MAX_ATTEMPTS = 5          # wrong guesses before a code is burned
    RESEND_COOLDOWN_SECONDS = 20  # min gap between codes for a phone

    @staticmethod
    def hash_code(raw: str) -> str:
        """Keyed HMAC-SHA256 of a one-time code. Keyed with OTP_HASH_KEY (which
        defaults to SECRET_KEY) so the tiny code space is not offline-brute-forceable
        from a DB leak. Codes are single-use and expire in EXPIRY_MINUTES, so —
        unlike the BVN/NIN hash — surviving a key rotation does not matter: a
        rotation only invalidates codes still in flight, which lapse in minutes."""
        key = getattr(settings, "OTP_HASH_KEY", "") or settings.SECRET_KEY
        return hmac.new(key.encode(), (raw or "").encode(), hashlib.sha256).hexdigest()

    def verify_code(self, raw: str) -> bool:
        """Constant-time compare of a submitted code against the stored hash.

        TEST-ONLY signup bypass: when BOTH settings.TEST_OTP["PHONE"] and ["CODE"]
        are set, that one phone accepts the fixed code only for SIGNUP. Password
        reset never accepts it; a leaked test pair therefore cannot take over an
        established account. Use is logged pseudonymously and go-live preflight
        hard-fails while configured.
        """
        test = settings.TEST_OTP
        if (self.purpose == self.SIGNUP and test["PHONE"] and test["CODE"]
                and self.phone == test["PHONE"]
                and hmac.compare_digest(raw or "", test["CODE"])):
            phone_fingerprint = hashlib.sha256(self.phone.encode()).hexdigest()[:12]
            log.warning("test_otp_bypass_used phone_sha256=%s purpose=%s — TEST_OTP is set; "
                        "REMOVE TEST_OTP_PHONE/TEST_OTP_CODE before go-live",
                        phone_fingerprint, self.purpose)
            return True
        return hmac.compare_digest(self.code_hash, self.hash_code(raw))

    @classmethod
    def issue(cls, phone, code, email="", purpose=None):
        """Create an OTP row, storing only the hash of `code`."""
        return cls.objects.create(
            phone=phone, code_hash=cls.hash_code(code),
            email=email, purpose=purpose or cls.SIGNUP)

    @property
    def is_expired(self) -> bool:
        return timezone.now() - self.created > timedelta(minutes=self.EXPIRY_MINUTES)

    @property
    def too_many_attempts(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def __str__(self):
        return f"{self.phone} · {self.purpose}"


class KnownDevice(models.Model):
    """A device install this user has authenticated from before.

    The point is the *negative*: "this account has never signed in from here" is the
    signal that separates a stolen password from the real customer on a new handset.
    A device being listed here proves nothing on its own — the id is a client-supplied
    value and an attacker on a compromised phone inherits a legitimate one.

    Rows are written by `common.risk.remember_device` on first sight and refreshed at
    most hourly, so this does not become a write per API call.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="known_devices")
    # The per-install id from lib/deviceIntegrity.ts (keychain-persisted, not a
    # hardware identifier).
    device_id = models.CharField(max_length=64)
    os = models.CharField(max_length=16, blank=True, default="")
    model = models.CharField(max_length=48, blank=True, default="")
    brand = models.CharField(max_length=32, blank=True, default="")
    last_ip = models.CharField(max_length=64, blank=True, default="")
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "device_id"], name="uniq_user_device"),
        ]
        indexes = [models.Index(fields=["user", "-last_seen"], name="device_user_seen_idx")]

    def __str__(self):
        return f"u_{self.user_id} {self.device_id[:12]} {self.model}"


class PushDevice(models.Model):
    """One Expo push token belonging to an authenticated app installation.

    Tokens can rotate and a shared handset can later sign in as another user, so
    ``token`` is globally unique and registration transfers ownership atomically.
    The install id lets logout revoke only this phone's notifications instead of
    silencing every device on the account.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="push_devices")
    token = models.CharField(max_length=255, unique=True)
    device_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    platform = models.CharField(max_length=16, blank=True, default="")
    enabled = models.BooleanField(default=True)
    created = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"u_{self.user_id} {self.platform} {self.token[:24]}…"


class OperatorTotp(models.Model):
    """A staff member's TOTP second factor.

    Scoped to OPERATORS, not customers: an operator session can credit wallets, decide
    KYC and change runtime settings, so a stolen operator password is worth far more
    than a customer's. Customers are protected by the transaction PIN instead, which is
    a second factor on the thing that matters (money) rather than on sign-in.

    The seed is application-encrypted before it reaches the database.  That does not
    defeat a fully compromised app host, but it materially protects SQL-only access,
    copied backups and accidental query exports.  Key material lives outside the
    database and supports a first-writes/rest-decrypt rotation list.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="operator_totp")
    secret = models.CharField(max_length=255)
    # Enrolment is two-step: a secret is issued, then proved with a live code. An
    # unconfirmed row must NOT gate login, or a half-finished enrolment locks the
    # operator out of the portal with no way back in.
    confirmed = models.BooleanField(default=False)
    # The last step consumed, so a code cannot be replayed inside its own window.
    last_step = models.BigIntegerField(default=0)
    created = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    def plaintext_secret(self) -> str:
        """Return the seed in memory, failing closed if ciphertext is damaged."""
        from cryptography.fernet import InvalidToken

        from .secret_fields import decrypt_operator_totp

        try:
            return decrypt_operator_totp(self.secret)
        except InvalidToken:
            log.error("operator_totp_decrypt_failed user=%s", self.user_id)
            return ""

    def save(self, *args, **kwargs):
        # Centralize encryption in the model so admin scripts and future call sites
        # cannot accidentally persist a raw seed.  Include the field when a narrow
        # update_fields save (e.g. burning a TOTP step) also upgrades legacy plaintext.
        from cryptography.fernet import InvalidToken

        from .secret_fields import decrypt_operator_totp, encrypt_operator_totp

        try:
            # Decrypt+encrypt also rotates a ciphertext written under an older key
            # whenever the operator next consumes a code and burns last_step.
            encrypted = encrypt_operator_totp(decrypt_operator_totp(self.secret))
        except InvalidToken:
            # Preserve damaged ciphertext for incident analysis.  Authentication
            # already fails closed in plaintext_secret().
            encrypted = self.secret
        changed = encrypted != self.secret
        self.secret = encrypted
        if changed and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"secret"}
        return super().save(*args, **kwargs)

    def __str__(self):
        state = "confirmed" if self.confirmed else "pending"
        return f"{self.user_id} totp ({state})"
