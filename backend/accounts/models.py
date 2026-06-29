import hashlib
import hmac
import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


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

    # KYC tier -> per-transaction limit (CBN-style; adjust to your licence).
    TIER_LIMITS = {1: Decimal("50000"), 2: Decimal("200000"), 3: Decimal("5000000")}
    # Per-day aggregate caps by tier, on top of the per-transaction limit.
    # WhatsApp onboarding (BVN -> Tier 2) caps at ₦1,000,000 transfers /
    # ₦100,000 bills a day; full app KYC (Tier 3) raises them. The caps live on
    # the user, so they apply identically in the app and on WhatsApp.
    DAILY_TRANSFER_LIMITS = {1: Decimal("50000"), 2: Decimal("1000000"), 3: Decimal("5000000")}
    DAILY_BILL_LIMITS = {1: Decimal("20000"), 2: Decimal("100000"), 3: Decimal("500000")}
    # Single transfers at/above this require step-up (face) verification.
    LARGE_TXN_THRESHOLD = Decimal("100000")

    # Transaction-PIN brute-force policy: after this many wrong PINs in a row,
    # lock further attempts for PIN_LOCKOUT_MINUTES. A stolen session token then
    # can't be used to guess the short PIN (10k combos) that gates money movement.
    PIN_MAX_ATTEMPTS = 5
    PIN_LOCKOUT_MINUTES = 15

    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    transaction_pin = models.CharField(max_length=128, blank=True, default="")
    pin_failed_attempts = models.PositiveSmallIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    # --- KYC ---
    tier = models.PositiveSmallIntegerField(default=1)
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

    # Profile photo: storage-relative path (e.g. "avatars/3-ab12.png"); resolved
    # to an absolute URL via MEDIA_URL when returned to the app.
    avatar = models.CharField(max_length=255, blank=True, default="")

    def set_transaction_pin(self, raw_pin: str) -> None:
        self.transaction_pin = make_password(raw_pin)

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
    def transaction_limit(self) -> Decimal:
        return self.TIER_LIMITS.get(self.tier, self.TIER_LIMITS[1])

    @property
    def daily_transfer_limit(self) -> Decimal:
        return self.DAILY_TRANSFER_LIMITS.get(self.tier, self.DAILY_TRANSFER_LIMITS[1])

    @property
    def daily_bill_limit(self) -> Decimal:
        return self.DAILY_BILL_LIMITS.get(self.tier, self.DAILY_BILL_LIMITS[1])

    def recompute_tier(self) -> None:
        """Tier 3 needs BVN + NIN; Tier 2 needs one of them; else Tier 1."""
        if self.bvn_verified and self.nin_verified:
            self.tier = 3
        elif self.bvn_verified or self.nin_verified:
            self.tier = 2
        else:
            self.tier = 1

    class Meta(AbstractUser.Meta):
        indexes = [
            # sign-in matches email case-insensitively (Q(email__iexact=...)); a
            # functional LOWER(email) index turns that branch from a full user-table
            # scan into an index lookup — sign-in is both hot and a brute-force
            # target, so the scan is a DoS amplifier as the user table grows.
            models.Index(Lower("email"), name="user_email_lower_idx"),
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

    key = models.CharField(max_length=64, unique=True, db_index=True)  # sha256 hex of the token
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tokens")
    created = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def _hash(raw: str) -> str:
        return hashlib.sha256((raw or "").encode()).hexdigest()

    @classmethod
    def issue(cls, user) -> "AccessToken":
        """Create a session token. The DB stores only the hash; the returned
        instance carries the RAW token on `.key` (in memory, unsaved) for the
        caller to hand to the client — do not re-save the instance afterwards."""
        raw = secrets.token_hex(32)
        tok = cls.objects.create(key=cls._hash(raw), user=user)
        tok.key = raw  # transient: expose the raw token to the caller without persisting it
        return tok

    @classmethod
    def resolve(cls, key: str):
        if not key:
            return None
        try:
            tok = cls.objects.select_related("user").get(key=cls._hash(key))
        except cls.DoesNotExist:
            return None
        ttl = timedelta(hours=settings.TOKEN_TTL_HOURS)
        if timezone.now() - tok.created > ttl:
            tok.delete()
            return None
        return tok.user

    def __str__(self):
        return f"{self.user} · {self.key[:8]}…"


class OTP(models.Model):
    """One-time code sent during phone verification or account recovery.

    `purpose` keeps a recovery code from being accepted by the signup verifier
    (which would mint a token for an existing account without a password) and
    vice-versa — each verifier filters to its own purpose.
    """

    SIGNUP = "signup"
    RESET = "reset"
    PURPOSES = [(SIGNUP, SIGNUP), (RESET, RESET)]

    phone = models.CharField(max_length=20, db_index=True)
    code = models.CharField(max_length=6)
    email = models.EmailField(blank=True, default="")
    purpose = models.CharField(max_length=10, choices=PURPOSES, default=SIGNUP)
    created = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)
    attempts = models.PositiveSmallIntegerField(default=0)

    EXPIRY_MINUTES = 10
    MAX_ATTEMPTS = 5          # wrong guesses before a code is burned
    RESEND_COOLDOWN_SECONDS = 20  # min gap between codes for a phone

    @property
    def is_expired(self) -> bool:
        return timezone.now() - self.created > timedelta(minutes=self.EXPIRY_MINUTES)

    @property
    def too_many_attempts(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def __str__(self):
        return f"{self.phone} · {self.code}"
