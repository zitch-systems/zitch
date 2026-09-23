import os
import secrets
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
TESTING = "test" in sys.argv
# Never read backend/.env, DATABASE_URL, Redis, bank keys, or production settings.
if os.environ.get("RENDER") or os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    raise ImproperlyConfigured("VAS harness is local-only; deployment is prohibited.")
if os.environ.get("DATABASE_URL"):
    raise ImproperlyConfigured("Unset DATABASE_URL before running the isolated VAS harness.")

SECRET_KEY = secrets.token_urlsafe(48)  # No sessions or signed persistence.
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"] + (["testserver"] if TESTING else [])
INSTALLED_APPS = ["vas_harness"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
ROOT_URLCONF = "vas_harness.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "Africa/Lagos"  # Explicit simulation assumption for naive bank timestamps.
APPEND_SLASH = False
DATA_UPLOAD_MAX_MEMORY_SIZE = 16 * 1024
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": BASE_DIR / "var" / "synthetic.sqlite3",
    "OPTIONS": {"timeout": 5},
}}
if not TESTING:
    (BASE_DIR / "var").mkdir(exist_ok=True)
TEST_RUNNER = "vas_harness.test_runner.NoNetworkRunner"
VAS_ENABLED = os.environ.get("WEMA_VAS_DEV_ENABLED", "false").lower() == "true"
VAS_TOKEN = os.environ.get("WEMA_VAS_DEV_TOKEN", "")
# There is deliberately NO live mode, outbound URL, or production provider fallback.
VAS_MODE = "synthetic"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "loggers": {"django.request": {"handlers": ["null"], "propagate": False}},
}
