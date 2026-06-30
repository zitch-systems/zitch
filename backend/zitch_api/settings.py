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
# Key for hashing government IDs (BVN/NIN) at rest — see accounts.hash_identifier.
# Defaults to SECRET_KEY (so existing hashes keep verifying), but should be set to
# its own value and PINNED: unlike SECRET_KEY it must never rotate, or every stored
# BVN/NIN hash becomes unverifiable (the raw value is intentionally not retained).
KYC_HASH_KEY = os.environ.get("DJANGO_KYC_HASH_KEY", "") or SECRET_KEY
# Secure by default: a real deploy must never accidentally run in debug (which
# would disable the HTTPS redirect, secure cookies, and the fail-closed provider
# mock guards below). Local dev opts in with DJANGO_DEBUG=true (see .env.example).
DEBUG = env_bool("DJANGO_DEBUG", False)

# Fail closed: an unset host list allows everything ONLY in debug (convenient for
# LAN/device testing). In production (DEBUG off) an unset DJANGO_ALLOWED_HOSTS
# yields an empty list so Django rejects unknown Host headers instead of silently
# trusting them. Render sets the env explicitly (and RENDER_HOST is appended).
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "*" if DEBUG else "").split(",")
    if h.strip()
]
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
    "banklink",
    "whatsapp",
    "portal",
    "console",
    "admin_api",
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

# Server-side strength rules, enforced via validate_password() in the password
# endpoints (the client's "letter + number" hints are advisory and bypassable by
# a direct API call). Blocks too-short, top-20k-common, all-numeric, and
# user-detail-similar passwords on a money account.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
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
# VTU / bills — VTU.ng (the sole VTU provider). Modern v2 REST/JSON API, JWT
# Bearer auth (no IP whitelisting). Either set a long-lived API token
# (VTUNG_API_KEY) or username+password (a JWT is fetched and refreshed
# automatically). Blank => MOCK mode, so the whole flow stays testable.
VTUNG = {
    "BASE_URL": os.environ.get("VTUNG_BASE_URL", "https://vtu.ng"),
    "API_KEY": os.environ.get("VTUNG_API_KEY", ""),
    "USERNAME": os.environ.get("VTUNG_USERNAME", ""),
    "PASSWORD": os.environ.get("VTUNG_PASSWORD", ""),
}
# Money-movement rail — Kora (Korapay): funding (checkout + virtual accounts),
# payouts, balances, identity (BVN/NIN/vNIN) and card issuing. Kora is the sole
# rail. Auth is a single static bearer secret key (no OAuth). One base URL serves
# both modes — the key prefix (sk_test_/sk_live_) selects test vs live. Blank
# SECRET_KEY => MOCK mode (see utility.kora). The PUBLIC_KEY is only needed for
# the few public-key endpoints (e.g. list banks) and is safe to expose.
KORA = {
    "BASE_URL": os.environ.get("KORA_BASE_URL", "https://api.korapay.com/merchant"),
    "SECRET_KEY": os.environ.get("KORA_SECRET_KEY", ""),
    "PUBLIC_KEY": os.environ.get("KORA_PUBLIC_KEY", ""),
}
# Virtual-card backend: "kora" or "issuer" (the generic CARD_ISSUER). Blank =>
# auto (issuer if configured, else Kora). See utility.providers.card_provider.
CARD_PROVIDER = os.environ.get("CARD_PROVIDER", "").strip().lower()
# Open banking — Mono: link an external bank, read balance/transactions, and fund
# the wallet from it via DirectPay (see utility.mono + the banklink app). The
# Connect widget runs client-side with PUBLIC_KEY; server calls use SECRET_KEY
# (mono-sec-key header). Blank SECRET_KEY => MOCK mode. WEBHOOK_SECRET is the
# shared secret compared against the mono-webhook-secret header.
MONO = {
    "BASE_URL": os.environ.get("MONO_BASE_URL", "https://api.withmono.com"),
    "SECRET_KEY": os.environ.get("MONO_SECRET_KEY", ""),
    "PUBLIC_KEY": os.environ.get("MONO_PUBLIC_KEY", ""),
    "WEBHOOK_SECRET": os.environ.get("MONO_WEBHOOK_SECRET", ""),
    # Bank-linking SIMULATION: when on, the Mono mock flow runs even in production
    # (DEBUG off) so the link/fund flow is fully testable in a real build without
    # real Mono keys. No real bank is contacted and no real money moves. Keep it
    # OFF for a live deployment; set MONO_SIMULATION=true to demo/test.
    "SIMULATION": env_bool("MONO_SIMULATION", False),
}
# SMS / OTP — Sendchamp.
SENDCHAMP = {
    "BASE_URL": os.environ.get("SENDCHAMP_BASE_URL", "https://api.sendchamp.com/api/v1"),
    "API_KEY": os.environ.get("SENDCHAMP_API_KEY", ""),
    # Default sender ID is "Zitch" — it must be an APPROVED Sender ID in
    # Sendchamp for the DND route to deliver; override with SENDCHAMP_SENDER_NAME
    # (e.g. an already-approved "SC-OTP") until "Zitch" is approved.
    "SENDER_NAME": os.environ.get("SENDCHAMP_SENDER_NAME", "Zitch"),
}
# Email / OTP fallback — Resend. Sends the same OTP code in parallel with
# Sendchamp so SMS delivery issues (DND, sender ID approval, carrier blocks)
# don't strand a user. Blank API_KEY => mock mode (silent success).
RESEND = {
    "BASE_URL": os.environ.get("RESEND_BASE_URL", "https://api.resend.com"),
    "API_KEY": os.environ.get("RESEND_API_KEY", ""),
    "FROM_EMAIL": os.environ.get("RESEND_FROM_EMAIL", "no-reply@zitch.ng"),
}
# KYC (BVN/NIN/liveness) — Prembly (IdentityPass). Blank => mock mode.
PREMBLY = {
    "BASE_URL": os.environ.get("PREMBLY_BASE_URL", "https://api.prembly.com"),
    "API_KEY": os.environ.get("PREMBLY_API_KEY", ""),
    "APP_ID": os.environ.get("PREMBLY_APP_ID", ""),
}
# BVN/NIN/vNIN verification is done by Kora Identity (see utility.providers.
# verify_bvn / verify_nin / verify_vnin). Prembly above is used only for the
# selfie/liveness step (kyc_verify_face), which Kora does not provide.
# Card issuer (virtual cards) — generic provider; blank => mock mode.
CARD_ISSUER = {
    "BASE_URL": os.environ.get("CARD_ISSUER_BASE_URL", ""),
    "API_KEY": os.environ.get("CARD_ISSUER_API_KEY", ""),
    "BRAND": os.environ.get("CARD_ISSUER_BRAND", "Verve"),
}

# WhatsApp Cloud API (Meta). Blank TOKEN => MOCK mode: outbound is logged and
# inbound signatures are accepted, so the channel is fully testable without a
# Meta app (same pattern as the other providers).
WHATSAPP = {
    "BASE_URL": os.environ.get("WHATSAPP_BASE_URL", "https://graph.facebook.com/v21.0"),
    "TOKEN": os.environ.get("WHATSAPP_TOKEN", ""),
    "PHONE_NUMBER_ID": os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
    "VERIFY_TOKEN": os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
    "APP_SECRET": os.environ.get("WHATSAPP_APP_SECRET", ""),
    "BUSINESS_NUMBER": os.environ.get("WHATSAPP_BUSINESS_NUMBER", ""),  # for wa.me deep links
}

# LLM intent layer for WhatsApp. Blank API_KEY => AI off (deterministic router
# handles everything). The model only proposes intents; it never moves money.
LLM = {
    "API_KEY": os.environ.get("LLM_API_KEY", ""),
    "MODEL": os.environ.get("LLM_MODEL", ""),
}

# FX rail — Fincra (multi-currency conversion). Blank SECRET_KEY => MOCK mode
# (deterministic rates, auto-settle) so conversion is testable without keys.
FINCRA = {
    "BASE_URL": os.environ.get("FINCRA_BASE_URL", "https://api.fincra.com"),
    "SECRET_KEY": os.environ.get("FINCRA_SECRET_KEY", ""),
    "BUSINESS_ID": os.environ.get("FINCRA_BUSINESS_ID", ""),
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Production security hardening ----------------------------------------
# All default ON whenever DEBUG is off (any real deploy) and stay OFF in local
# dev and tests. Render terminates TLS at its proxy and forwards
# X-Forwarded-Proto, which SECURE_PROXY_SSL_HEADER (above) teaches Django to
# trust — so the HTTPS redirect and secure-cookie flags behave correctly behind
# it. Each stays env-overridable for unusual setups (e.g. a non-TLS network).
# "Production" = a real deploy: debug off AND not the test runner. Folding
# `not TESTING` in keeps the suite (which Django runs with DEBUG off) from
# tripping the fail-fast guards while still hardening every real deploy.
_PROD = not DEBUG and not TESTING
SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", _PROD)
# Keep the "/healthz" liveness probe answering 200 over plain HTTP so a platform
# health check never trips on the HTTPS redirect (it returns booleans only, no
# secrets). The marketing landing at "/" redirects to HTTPS like everything else.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SESSION_COOKIE_SECURE = env_bool("DJANGO_SESSION_COOKIE_SECURE", _PROD)
CSRF_COOKIE_SECURE = env_bool("DJANGO_CSRF_COOKIE_SECURE", _PROD)
# HSTS — tell browsers to use HTTPS only. One year, including subdomains; preload
# stays opt-in (it's the hard-to-reverse part — enable once you're ready to
# submit the domain to the browser preload list).
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", str(31536000 if _PROD else 0)))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_HSTS_INCLUDE_SUBDOMAINS", _PROD)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_HSTS_PRELOAD", False)
# W021 nags to enable HSTS preload; it's a deliberate opt-in (submitting to the
# browser preload list is hard to undo), gated behind DJANGO_HSTS_PRELOAD above.
# Silence the nag so `check --deploy` stays a green, meaningful CI gate.
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Fail fast: a real deploy must never run on the insecure dev SECRET_KEY — a
# loud boot error beats a silently forgeable signing key.
if _PROD and SECRET_KEY == "dev-insecure-change-me":
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a strong value when DJANGO_DEBUG is off.")

# Fail fast: the public WhatsApp webhook verifies Meta's HMAC signature only when
# APP_SECRET is set. In mock/dev (no secret) unsigned callbacks are accepted for
# testability — that must NEVER happen in production, where a forged callback can
# impersonate any linked user's number. Require the secret (and verify token)
# whenever the channel is live or DEBUG is off.
if _PROD and (WHATSAPP["TOKEN"] or WHATSAPP["PHONE_NUMBER_ID"]):
    from django.core.exceptions import ImproperlyConfigured

    if not WHATSAPP["APP_SECRET"]:
        raise ImproperlyConfigured("WHATSAPP_APP_SECRET must be set when the WhatsApp channel is live (signature verification).")
    if not WHATSAPP["VERIFY_TOKEN"]:
        raise ImproperlyConfigured("WHATSAPP_VERIFY_TOKEN must be set when the WhatsApp channel is live (webhook handshake).")

# --- Logging --------------------------------------------------------------
# A fintech must be able to answer "who tried to brute-force this account / which
# payout failed / why did this 500" after the fact. Render (and most PaaS)
# capture stdout, so log there and ship to a retained aggregator via a log drain.
# The `zitch` logger carries app + security/money-audit events (auth failures,
# PIN lockouts, settled money movements); tune verbosity with DJANGO_LOG_LEVEL.
LOG_LEVEL = os.environ.get("DJANGO_LOG_LEVEL", "INFO").upper()
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "standard"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        # 5xx are reported (and, when wired, captured by Sentry below).
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        # Application + security/money-audit stream.
        "zitch": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "whatsapp": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# --- Error reporting (Sentry) --------------------------------------------
# Enabled only when SENTRY_DSN is set AND sentry-sdk is installed, so dev/test
# never phone home. PII is never sent (fintech); add SENTRY_DSN (sync:false) in
# the Render dashboard to turn it on.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN and not TESTING:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.django import DjangoIntegration

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[DjangoIntegration()],
            environment=os.environ.get("SENTRY_ENVIRONMENT", "production" if _PROD else "development"),
            traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
            send_default_pii=False,
        )
    except Exception:  # noqa: BLE001 — observability must never break boot
        pass
