"""
Django settings for the Zitch API.

Local SQLite by default; Render + Postgres in production via environment
variables. See .env.example for the full list.
"""
import os
import sys
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",") if h.strip()]
RENDER_HOST = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if RENDER_HOST:
    ALLOWED_HOSTS.append(RENDER_HOST)

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()
]
if RENDER_HOST:
    CSRF_TRUSTED_ORIGINS.append(f"https://{RENDER_HOST}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "accounts",
    "wallet",
    "utility",
    "exams",
    "loans",
    "savings",
    "betting",
    "transfers",
    "cards",
    "convert",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# CORS: Expo web (Metro 8081 / web-build 19006) calls the API cross-origin.
# In DEBUG we accept any origin so emulator, web, and LAN devices all work.
# In prod, set CORS_ALLOWED_ORIGINS as a comma-separated env var.
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()
]
CORS_ALLOW_CREDENTIALS = False

ROOT_URLCONF = "zitch_api.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "zitch_api.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
        ssl_require=env_bool("DJANGO_DB_SSL", not DEBUG and bool(os.environ.get("DATABASE_URL"))),
    )
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

# User-uploaded media (profile photos). Served by Django in DEBUG; in production
# the local disk is ephemeral on most PaaS, so point DEFAULT_FILE_STORAGE at S3
# (or similar) before relying on avatars persisting across deploys.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- App-specific config ---
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "24"))

# Per-IP rate limiting (see common/ratelimit). Off under tests so the shared
# process cache can't bleed counts across unrelated cases; a dedicated test
# re-enables it. In production, back the cache with Redis (or rate-limit at the
# edge) for accurate limits across workers.
TESTING = "test" in sys.argv
# Force-off under tests regardless of any RATELIMIT_ENABLE in the environment /
# .env, so a dev's local rate-limit setting can't bleed shared cache counts into
# unrelated test cases (RateLimitTests opts back in via override_settings).
RATELIMIT_ENABLE = False if TESTING else env_bool("RATELIMIT_ENABLE", True)

# Third-party credentials. Blank key => that integration runs in MOCK mode so
# the full flow is testable without external accounts.
# VTU / bills aggregator — Baxi.
BAXI = {
    "BASE_URL": os.environ.get("BAXI_BASE_URL", "https://payments.baxipay.com.ng/api/baxipay"),
    "API_KEY": os.environ.get("BAXI_API_KEY", ""),
}
# Payments (wallet funding) — Monnify.
MONNIFY = {
    "BASE_URL": os.environ.get("MONNIFY_BASE_URL", "https://sandbox.monnify.com"),
    "API_KEY": os.environ.get("MONNIFY_API_KEY", ""),
    "SECRET_KEY": os.environ.get("MONNIFY_SECRET_KEY", ""),
    "CONTRACT_CODE": os.environ.get("MONNIFY_CONTRACT_CODE", ""),
    "REDIRECT_URL": os.environ.get("MONNIFY_REDIRECT_URL", ""),
    # Monnify wallet account funds are disbursed from (bank transfers / payouts).
    "SOURCE_ACCOUNT": os.environ.get("MONNIFY_SOURCE_ACCOUNT", ""),
}
# SMS / OTP — Sendchamp.
SENDCHAMP = {
    "BASE_URL": os.environ.get("SENDCHAMP_BASE_URL", "https://api.sendchamp.com/api/v1"),
    "API_KEY": os.environ.get("SENDCHAMP_API_KEY", ""),
    "SENDER_NAME": os.environ.get("SENDCHAMP_SENDER_NAME", "Sendchamp"),
}
# KYC (BVN/NIN/liveness) — Prembly (IdentityPass). Blank => mock mode.
PREMBLY = {
    "BASE_URL": os.environ.get("PREMBLY_BASE_URL", "https://api.prembly.com"),
    "API_KEY": os.environ.get("PREMBLY_API_KEY", ""),
    "APP_ID": os.environ.get("PREMBLY_APP_ID", ""),
}
# Card issuer (virtual cards) — provider TBD. Blank => mock mode.
CARD_ISSUER = {
    "BASE_URL": os.environ.get("CARD_ISSUER_BASE_URL", ""),
    "API_KEY": os.environ.get("CARD_ISSUER_API_KEY", ""),
    "BRAND": os.environ.get("CARD_ISSUER_BRAND", "Verve"),
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
