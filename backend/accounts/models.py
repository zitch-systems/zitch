import secrets
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user keyed by phone, with a hashed transaction PIN.

    `username` is kept (Django requires it) but we authenticate by phone/email.
    """

    # KYC tier -> per-transaction limit (CBN-style; adjust to your licence).
    TIER_LIMITS = {1: Decimal("50000"), 2: Decimal("200000"), 3: Decimal("5000000")}
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
    bvn = models.CharField(max_length=11, blank=True, default="")
    bvn_verified = models.BooleanField(default=False)
    nin = models.CharField(max_length=11, blank=True, default="")
    nin_verified = models.BooleanField(default=False)
    face_verified = models.BooleanField(default=False)

    def set_transaction_pin(self, raw_pin: str) -> None:
        self.transaction_pin = make_password(raw_pin)

    def check_transaction_pin(self, raw_pin: str) -> bool:
        if not self.transaction_pin:
            return False
        return check_password(raw_pin, self.transaction_pin)

    @property
    def pin_locked(self) -> bool:
        """True while the transaction PIN is temporarily locked after too many
        wrong attempts (see PIN_MAX_ATTEMPTS / PIN_LOCKOUT_MINUTES)."""
        return self.pin_locked_until is not None and timezone.now() < self.pin_locked_until

    @property
    def transaction_limit(self) -> Decimal:
        return self.TIER_LIMITS.get(self.tier, self.TIER_LIMITS[1])

    def recompute_tier(self) -> None:
        """Tier 3 needs BVN + NIN; Tier 2 needs one of them; else Tier 1."""
        if self.bvn_verified and self.nin_verified:
            self.tier = 3
        elif self.bvn_verified or self.nin_verified:
            self.tier = 2
        else:
            self.tier = 1

    def __str__(self):
        return self.phone or self.email or self.username


class AccessToken(models.Model):
    """Opaque bearer token returned to the app as `access_token`."""

    key = models.CharField(max_length=64, unique=True, db_index=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tokens")
    created = models.DateTimeField(auto_now_add=True)

    @classmethod
    def issue(cls, user) -> "AccessToken":
        return cls.objects.create(key=secrets.token_hex(32), user=user)

    @classmethod
    def resolve(cls, key: str):
        if not key:
            return None
        try:
            tok = cls.objects.select_related("user").get(key=key)
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
    """One-time code sent during phone verification."""

    phone = models.CharField(max_length=20, db_index=True)
    code = models.CharField(max_length=6)
    email = models.EmailField(blank=True, default="")
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
