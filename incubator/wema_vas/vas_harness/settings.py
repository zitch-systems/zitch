import os
import re
import secrets
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv
# Never read backend/.env, DATABASE_URL, Redis, or the existing provider keys.
if os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("VAS uses only its own dedicated database settings, not DATABASE_URL.")

VAS_MODE = os.environ.get("WEMA_VAS_MODE", "synthetic")
if VAS_MODE not in {"synthetic", "validation"}:
    raise ImproperlyConfigured("Unknown VAS mode")
if VAS_MODE == "synthetic" and (os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_HOSTNAME")):
    raise ImproperlyConfigured("The synthetic service may only run locally.")
VAS_PREFIX = os.environ.get("WEMA_VAS_PREFIX", "711")
if not re.fullmatch(r"[0-9]{3}", VAS_PREFIX):
    raise ImproperlyConfigured("VAS prefix must be exactly three digits.")
VAS_ENABLED = os.environ.get("WEMA_VAS_ENABLED", "false").lower() == "true" if VAS_MODE == "validation" else os.environ.get("WEMA_VAS_DEV_ENABLED", "false").lower() == "true"
VAS_TOKEN = os.environ.get("WEMA_VAS_BANK_TOKEN", "") if VAS_MODE == "validation" else os.environ.get("WEMA_VAS_DEV_TOKEN", "")
VAS_IDENTITY_KEYS = os.environ.get("WEMA_VAS_IDENTITY_KEYS", "")
if VAS_MODE == "validation":
    if len(VAS_TOKEN) < 48 or not VAS_IDENTITY_KEYS:
        raise ImproperlyConfigured("Validation requires a distinct bank token and identity encryption keys.")
    try:
        for key in VAS_IDENTITY_KEYS.split(","):
            Fernet(key.strip().encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured("Invalid VAS identity key") from exc

SECRET_KEY = os.environ.get("WEMA_VAS_DJANGO_SECRET", "") if VAS_MODE == "validation" else secrets.token_urlsafe(48)
if VAS_MODE == "validation" and len(SECRET_KEY) < 44:
    raise ImproperlyConfigured("Validation requires an isolated Django secret.")
DEBUG = False
ALLOWED_HOSTS = ([host.strip() for host in os.environ.get("WEMA_VAS_ALLOWED_HOSTS", "").split(",") if host.strip()]
                 if VAS_MODE == "validation" else ["localhost", "127.0.0.1", "[::1"])
if TESTING:
    ALLOWED_HOSTS.append("testserver")
if VAS_MODE == "validation" and (not ALLOWED_HOSTS or any(host == "*" or host.startswith(".") for host in ALLOWED_HOSTS)):
    raise ImproperlyConfigured("Set explicit validation service hostnames.")
INSTALLED_APPS = ["vas_harness"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "vas_harness.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Africa/Lagos"  # Explicit simulation assumption for naive bank timestamps.
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024
if VAS_MODE == "validation":
    db_parts = {name: os.environ.get("WEMA_VAS_DB_" + name, "") for name in ("NAME", "USER", "PASSWORD", "HOST", "PORT")}
    if (any(not value for value in db_parts.values()) or not db_parts["PORT"].isdigit()
            or not db_parts["NAME"].startswith("zitch_vas_")):
        raise ImproperlyConfigured("A dedicated VAS PostgreSQL database is required.")
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql", **db_parts,
        "OPTIONS": {"sslmode": os.environ.get("WEMA_VAS_DB_SSLMODE", "require")},
        "CONN_MAX_AGE": 0,
    }}
    if DATABASES["default"]["OPTIONS"]["sslmode"] not in ("require", "verify-full"):
        raise ImproperlyConfigured("Validation PostgreSQL must use TLS.")
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "var" / "synthetic.sqlite3",
        "OPTIONS": {"timeout": 5},
    }}
    if not TESTING:
        (BASE_DIR / "var").mkdir(exist_ok=True)
TEST_RUNNER = "vas_harness.test_runner.NoNetworkRunner"
# Set only when the reverse proxy strips incoming forwarded headers and sets its own.
if VAS_MODE == "validation" and os.environ.get("WEMA_VAS_TRUST_TLS_PROXY") == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
VAS_REQUIRE_HTTPS = VAS_MODE == "validation"
# No outbound URL, provider fallback, or automatic production activation.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "loggers": {"django.request": {"handlers": ["null"], "propagate": False}},
}
