import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user keyed by phone, with a hashed transaction PIN.

    `username` is kept (Django requires it) but we authenticate by phone/email.
    """

    phone = models.CharField(max_length=20, unique=True, null=True, blank=True)
    transaction_pin = models.CharField(max_length=128, blank=True, default="")

    def set_transaction_pin(self, raw_pin: str) -> None:
        self.transaction_pin = make_password(raw_pin)

    def check_transaction_pin(self, raw_pin: str) -> bool:
        if not self.transaction_pin:
            return False
        return check_password(raw_pin, self.transaction_pin)

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

    EXPIRY_MINUTES = 10

    @property
    def is_expired(self) -> bool:
        return timezone.now() - self.created > timedelta(minutes=self.EXPIRY_MINUTES)

    def __str__(self):
        return f"{self.phone} · {self.code}"
