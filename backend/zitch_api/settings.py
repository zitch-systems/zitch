"""
Django settings for the Zitch API.

Local SQLite by default; Render + Postgres in production via environment
variables. See .env.example for the full list.
"""
import os
import sys
from decimal import Decimal
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
# Key for hashing one-time codes (signup / password-reset OTPs) at rest — see
# accounts.OTP.hash_code. Defaults to SECRET_KEY. Unlike KYC_HASH_KEY it may rotate
# freely: codes are single-use and expire in minutes, so a rotation only invalidates
# codes still in flight.
OTP_HASH_KEY = os.environ.get("DJANGO_OTP_HASH_KEY", "") or SECRET_KEY
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
    "compliance",
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

# /admin/ is the only login surface that goes through authenticate(); the API
# login views resolve the identifier themselves. Stock ModelBackend matches on
# username alone, so an operator seeded with DJANGO_SUPERUSER_EMAIL could sign
# into the operator portal with their email and still be refused by /admin/.
# This backend accepts username, email or phone — see accounts/auth_backends.py.
AUTHENTICATION_BACKENDS = ["accounts.auth_backends.UsernameOrEmailOrPhoneBackend"]

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
# Admin/operator (staff-scoped) tokens expire far sooner than app sessions — a
# leaked back-office token is high blast-radius, so keep the window short.
ADMIN_TOKEN_TTL_HOURS = int(os.environ.get("ADMIN_TOKEN_TTL_HOURS", "2"))

# Manual wallet credit guardrails (admin_api.wallet_credit). A single manual
# credit can't exceed ADMIN_MAX_MANUAL_CREDIT, and one operator's manual credits
# can't exceed ADMIN_MANUAL_CREDIT_DAILY_CAP in a rolling 24h — so a compromised
# or rogue operator account can't mint unlimited balance in one action or a
# drip. Raise via env for a treasury operator if genuinely needed.
ADMIN_MAX_MANUAL_CREDIT = int(os.environ.get("ADMIN_MAX_MANUAL_CREDIT", "500000"))
ADMIN_MANUAL_CREDIT_DAILY_CAP = int(os.environ.get("ADMIN_MANUAL_CREDIT_DAILY_CAP", "2000000"))

# Per-account login lockout (common.ratelimit). After ADMIN_LOGIN_MAX_FAILS bad
# attempts against one operator identifier, further attempts are refused for
# ADMIN_LOGIN_LOCKOUT_SECONDS regardless of source IP.
ADMIN_LOGIN_MAX_FAILS = int(os.environ.get("ADMIN_LOGIN_MAX_FAILS", "5"))
ADMIN_LOGIN_LOCKOUT_SECONDS = int(os.environ.get("ADMIN_LOGIN_LOCKOUT_SECONDS", "900"))
USER_LOGIN_MAX_FAILS = int(os.environ.get("USER_LOGIN_MAX_FAILS", "8"))
USER_LOGIN_LOCKOUT_SECONDS = int(os.environ.get("USER_LOGIN_LOCKOUT_SECONDS", "900"))

# Per-IP rate limiting (see common/ratelimit). Off under tests so the shared
# process cache can't bleed counts across unrelated cases; a dedicated test
# re-enables it. In production, back the cache with Redis (or rate-limit at the
# edge) for accurate limits across workers.
TESTING = "test" in sys.argv
# Force-off under tests regardless of any RATELIMIT_ENABLE in the environment /
# .env, so a dev's local rate-limit setting can't bleed shared cache counts into
# unrelated test cases (RateLimitTests opts back in via override_settings).
RATELIMIT_ENABLE = False if TESTING else env_bool("RATELIMIT_ENABLE", True)
# Number of trusted reverse-proxy hops represented at the RIGHT side of
# X-Forwarded-For. Render is one hop. Taking the configured right-side entry
# prevents a client from prepending a fake address to rotate rate-limit buckets.
RATELIMIT_TRUSTED_PROXY_HOPS = max(
    0, int(os.environ.get("RATELIMIT_TRUSTED_PROXY_HOPS", "1"))
)

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
# Virtual-card backend: "wema" or "issuer". Explicit value wins; blank => AUTO (Wema's
# Virtual Naira Card once WEMA_CARD_KEY is set, else the generic CARD_ISSUER — so cards
# never break on a deploy without a Wema card key). See utility.providers.card_provider.
CARD_PROVIDER = os.environ.get("CARD_PROVIDER", "").strip().lower()
# Wallet FUND-IN backend — "wema" (the sole rail); blank => wema. Wema funds by bank
# transfer to an OTP-provisioned NUBAN (credited by the reconcile_wema poller). See
# utility.providers.payment_provider.
PAYMENT_PROVIDER = os.environ.get("PAYMENT_PROVIDER", "").strip().lower()
# The bank-PAYOUT + recipient name-enquiry rail — "wema" (the sole rail); blank =>
# wema. Set WEMA_CHANNEL_ID + WEMA_WALLET_KEY + WEMA_SOURCE_ACCOUNT (or
# WEMA_SIMULATION=true to test the flow without live keys). See
# utility.providers.payout_provider.
PAYOUT_PROVIDER = os.environ.get("PAYOUT_PROVIDER", "").strip().lower()
# The VAS (airtime/data/bills) rail — "wema" or "vtung". Explicit value wins; blank
# => AUTO (Wema once its VAS keys are set, else VTU.ng, so nothing breaks without
# keys). When Wema is used: airtime always routes to Wema; data/cable route to Wema
# once `manage.py seed_wema_plans` has synced each plan's wema_code; electricity/
# betting stay on VTU.ng until their Wema billers are mapped. See
# utility.providers.vas_provider and docs/wema-migration.md.
VAS_PROVIDER = os.environ.get("VAS_PROVIDER", "").strip().lower()
# The BVN/NIN KYC rail — "wema"; blank => wema. ALAT has no standalone identity lookup,
# so BVN/NIN are verified by the NUBAN account-creation flow (name-matched before the
# tier lifts). The image / biometric steps (selfie/liveness, address, ID-document) stay
# on Prembly. See utility.providers.kyc_provider and docs/wema-migration.md.
KYC_PROVIDER = os.environ.get("KYC_PROVIDER", "").strip().lower()
# Fraud velocity guard: max outbound money movements per user per 10 minutes
# (common.http.check_velocity, applied on every send path). 0 disables. Off in
# tests (suites legitimately hammer one user far faster than any human).
VELOCITY_MAX_OUT_10MIN = 0 if TESTING else int(os.environ.get("VELOCITY_MAX_OUT_10MIN", "20"))
# (Observability: the Sentry init lives at the bottom of this file — already wired.)
# Wema / ALAT (Banking-as-a-Service) — the SOLE money-movement rail: fund-in
# (dedicated NUBANs via a BVN/NIN + OTP flow, which also verifies identity — ALAT has
# no standalone KYC lookup) AND bank payouts + recipient name enquiry + balance, VAS
# (airtime/data/bills) and a NUBAN-keyed virtual card. Azure APIM: a per-PRODUCT
# subscription key (Ocp-Apim-Subscription-Key) + a channel id (x-api-key / access).
# Sandbox host https://apiplayground.alat.ng (LIVE differs — set WEMA_BASE_URL).
# There is NO inbound-credit webhook: deposits AND payout settlement are handled by
# the reconcile_wema poller. Blank keys => MOCK; WEMA_SIMULATION=true serves the mock
# flow even in prod (no real money). securityInfo (encrypted, required on
# money-movement calls) is not in the OpenAPI — set WEMA_SECURITY_INFO once Wema
# supplies the scheme. See utility.wema.
WEMA = {
    "BASE_URL": os.environ.get("WEMA_BASE_URL", "https://apiplayground.alat.ng"),
    "CHANNEL_ID": os.environ.get("WEMA_CHANNEL_ID", ""),   # x-api-key / access value
    "KEYS": {
        "wallet": os.environ.get("WEMA_WALLET_KEY", ""),   # Wallet Services — bundles 13 APIs (see _WALLET_COVERED)
        "card": os.environ.get("WEMA_CARD_KEY", ""),       # Virtual Naira Card — its OWN product, no wallet fallback
        "airtime": os.environ.get("WEMA_AIRTIME_KEY", ""), # optional — falls back to the wallet key
        "bills": os.environ.get("WEMA_BILLS_KEY", ""),     # optional — falls back to the wallet key
        "kyc": os.environ.get("WEMA_KYC_KEY", ""),         # optional — falls back to the wallet key
        "remita": os.environ.get("WEMA_REMITA_KEY", ""),   # optional — falls back to the wallet key
        "bnpl": os.environ.get("WEMA_BNPL_KEY", ""),       # Buy-Now-Pay-Later APIM subscription
    },
    # The card product id ("cardKey") ALAT's virtualCard request expects — distinct
    # from the subscription key above. Supplied by Wema; blank until then.
    "CARD_PRODUCT_KEY": os.environ.get("WEMA_CARD_PRODUCT_KEY", ""),
    # BNPL uses a DIFFERENT auth scheme from the rest of the rail: merchant headers
    # (x-merchant-id + x-merchant-authorization-key) alongside the APIM subscription
    # key above. Supplied by Wema when the BNPL product is enabled.
    "BNPL_MERCHANT_ID": os.environ.get("WEMA_BNPL_MERCHANT_ID", ""),
    "BNPL_AUTH_KEY": os.environ.get("WEMA_BNPL_AUTH_KEY", ""),
    # Our own Wema pool/settlement NUBAN that funds outbound transfers (the
    # sourceAccountNumber on ProcessClientTransfer). Required for Wema payouts.
    "SOURCE_ACCOUNT": os.environ.get("WEMA_SOURCE_ACCOUNT", ""),
    "SECURITY_INFO": os.environ.get("WEMA_SECURITY_INFO", ""),
    "SIMULATION": env_bool("WEMA_SIMULATION", False),
    # Bank-called callbacks (see wallet/wema_callbacks.py). ALAT signs nothing, so the
    # secret lives in the URL path the bank profiles; _PREV keeps a rotation overlap.
    "CALLBACK_TOKEN": os.environ.get("WEMA_CALLBACK_TOKEN", ""),
    "CALLBACK_TOKEN_PREV": os.environ.get("WEMA_CALLBACK_TOKEN_PREV", ""),
    # Source-IP allowlist of the bank's gateway egress addresses. Production is
    # fail-closed by default; use the profiling environment to validate the trusted
    # proxy hop and observed Wema source before enabling live money.
    "CALLBACK_ENFORCE_IPS": env_bool(
        "WEMA_CALLBACK_ENFORCE_IPS", not DEBUG and not TESTING),
    "CALLBACK_IPS": [ip for ip in os.environ.get(
        "WEMA_CALLBACK_IPS", "135.236.18.76,74.178.162.156").split(",") if ip.strip()],
    # Payout authorisation: only authorise a PENDING bank payout younger than this
    # (seconds). In production a missing/mismatched value denies the payout: Wema
    # confirmed this is a private value chosen by Zitch and echoed to the callback.
    "AUTH_MAX_AGE": int(os.environ.get("WEMA_AUTH_MAX_AGE", "900") or 900),
    "AUTH_REQUIRE_SECURITY_INFO": env_bool(
        "WEMA_AUTH_REQUIRE_SECURITY_INFO", not DEBUG and not TESTING),
    # VAS status legends. ALAT's PartnerPayment status checks answer with a bare
    # INTEGER `transactionStatus` — 1..11 for airtime/data, 1..9 for bills — and do
    # not publish what the numbers mean. Without the legend a timed-out VAS purchase
    # can never be auto-settled or auto-refunded: it stays PENDING forever, because
    # guessing either way loses money (settle a failure and the customer is debited
    # for nothing; refund a delivered top-up and we pay twice).
    #
    # These exist so that the moment Wema supplies the legend it is a Render env var,
    # not a code change and a deploy. Format is `code=outcome` pairs, comma or space
    # separated, where outcome is exactly one of success | pending | failed:
    #
    #   WEMA_VAS_STATUS_LEGEND="1=success,2=pending,3=failed,4=failed"
    #
    # Codes absent from the legend keep today's behaviour (PENDING, logged), and an
    # unparseable entry is dropped with an error rather than defaulting — so a typo
    # can never turn an unknown code into a settlement. See utility.wema._vas_legend.
    "VAS_STATUS_LEGEND": os.environ.get("WEMA_VAS_STATUS_LEGEND", ""),
    "BILLS_STATUS_LEGEND": os.environ.get("WEMA_BILLS_STATUS_LEGEND", ""),
}
# Fraud: a spend at or above this from a device the account has NEVER authenticated
# from requires face verification first (see common.risk.new_device_step_up_error).
# The shape of the fraud it addresses is a stolen password arriving on a new handset
# and moving as much as the tier allows — which the tier cap alone cannot see.
# Deliberately BELOW LARGE_TXN_THRESHOLD (₦100,000): the point is to catch a drain
# that stays under the existing face gate. Set to 0 to disable.
RISK_NEW_DEVICE_FACE_THRESHOLD = Decimal(
    os.environ.get("RISK_NEW_DEVICE_FACE_THRESHOLD", "50000") or "0")
# Operator second factor (TOTP). OFF by default: switching it on before operators have
# enrolled locks every one of them out of the portal at once, including whoever would
# have to fix it. With it on, only money-/settings-capable roles are required — see
# admin_api.views._mfa_required_for. An operator who HAS enrolled is always challenged
# regardless of this flag.
OPS_REQUIRE_MFA = env_bool("OPS_REQUIRE_MFA", False)
# Maker/checker on high-risk operator actions (currently: a manual wallet credit above
# ADMIN_MAX_MANUAL_CREDIT, which today is simply refused). OFF by default because a
# queue nobody can drain silently breaks a working workflow — the approval queue is
# reachable via /api/{ops,admin}/approvals and Django admin, but the portal SPA has no
# screen for it yet. See common.approvals and docs/operator-controls.md.
OPS_REQUIRE_DUAL_APPROVAL = env_bool("OPS_REQUIRE_DUAL_APPROVAL", False)

# AML transaction monitoring (compliance.services). The threshold is the one the
# compliance QA response gives a regulator: "threshold breaches (₦5M+)". Raising a case
# does NOT block the transaction — see docs/compliance-operations.md for why, and for the
# one place the code and the policy wording differ.
AML_THRESHOLD_NGN = Decimal(os.environ.get("AML_THRESHOLD_NGN", "5000000"))
AML_VELOCITY_MIN_ROWS_24H = int(os.environ.get("AML_VELOCITY_MIN_ROWS_24H", "40"))
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
# SMS rail. "termii" | "sendchamp"; blank => AUTO (termii when its key is set, else
# sendchamp). Both are kept wired so an unapproved sender ID on one is a one-env-var
# switch back rather than a deploy — sender-ID approval is the usual reason OTPs stop
# arriving, and it is a carrier decision we don't control.
SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "").strip().lower()
TERMII = {
    # Termii serves several regional hosts and the dashboard shows the one for YOUR
    # account, so this is configurable rather than assumed.
    "BASE_URL": os.environ.get("TERMII_BASE_URL", "https://v3.api.termii.com").rstrip("/"),
    "API_KEY": os.environ.get("TERMII_API_KEY", "").strip(),
    "SENDER_ID": os.environ.get("TERMII_SENDER_ID", "Zitch").strip(),
    # "dnd" is the TRANSACTIONAL route and the only correct one for OTP: most Nigerian
    # lines are DND-registered, and Termii documents the "generic" route as promotional
    # only. The sender ID must be whitelisted for DND or messages are accepted and never
    # delivered — the same failure mode Sendchamp had.
    "CHANNEL": os.environ.get("TERMII_CHANNEL", "dnd").strip() or "dnd",
}
SENDCHAMP = {
    "BASE_URL": os.environ.get("SENDCHAMP_BASE_URL", "https://api.sendchamp.com/api/v1"),
    "API_KEY": os.environ.get("SENDCHAMP_API_KEY", ""),
    # Default sender ID is "Zitch" — it must be an APPROVED Sender ID in
    # Sendchamp for the DND route to deliver; override with SENDCHAMP_SENDER_NAME
    # (e.g. an already-approved "SC-OTP") until "Zitch" is approved.
    "SENDER_NAME": os.environ.get("SENDCHAMP_SENDER_NAME", "Zitch"),
}
# TEST-ONLY OTP bypass — LEAVE UNSET IN PRODUCTION. When BOTH TEST_OTP_PHONE and
# TEST_OTP_CODE are set, that ONE phone number accepts that fixed OTP code in every
# OTP flow, so the app can be walked end-to-end while a real SMS sender ID awaits
# carrier approval. Every other number still requires a real delivered code. Use is
# logged loudly (zitch.security) and `manage.py wema_preflight` HARD-FAILS while it
# is set, so it cannot slip into go-live. Remove both env vars before launch.
# With DEBUG off these two ALSO require ALLOW_PRODUCTION_TEST_OTP=true or the app
# refuses to boot — see production_checks.validate_production_configuration.
TEST_OTP = {
    "PHONE": os.environ.get("TEST_OTP_PHONE", "").strip(),
    "CODE": os.environ.get("TEST_OTP_CODE", "").strip(),
}
# TEST-ONLY simulated-deposit token — LEAVE UNSET IN PRODUCTION. When set (AND
# WEMA_SIMULATION is on), POST /api/dev/simulate-deposit/ with this token credits a
# mock wallet deposit, so the app can be walked fund -> transfer -> airtime end to
# end with fake money. The endpoint 404s whenever WEMA_SIMULATION is off, so it can
# never fabricate real money on a live deploy. `manage.py wema_preflight` HARD-FAILS
# while this is set. Remove it before launch.
SIMULATE_DEPOSIT_TOKEN = os.environ.get("SIMULATE_DEPOSIT_TOKEN", "").strip()
# Email / OTP fallback — Resend. Sends the same OTP code in parallel with
# Sendchamp so SMS delivery issues (DND, sender ID approval, carrier blocks)
# don't strand a user. Blank API_KEY => mock mode (silent success).
# FROM_EMAIL MUST be on a Resend-verified domain or every send is REJECTED (dropped).
# The verified sending domain is `send.zitch.ng` (DKIM + SPF verified) — NOT the bare
# `zitch.ng` root — so the default sends from a `send.zitch.ng` address with a friendly
# "Zitch" display name. Resend accepts the "Name <addr>" form.
RESEND = {
    "BASE_URL": os.environ.get("RESEND_BASE_URL", "https://api.resend.com"),
    "API_KEY": os.environ.get("RESEND_API_KEY", ""),
    "FROM_EMAIL": os.environ.get("RESEND_FROM_EMAIL", "Zitch <no-reply@send.zitch.ng>"),
}
# KYC — selfie/liveness + address + ID-document (image/biometric) — Prembly
# (IdentityPass). Blank => mock mode.
PREMBLY = {
    "BASE_URL": os.environ.get("PREMBLY_BASE_URL", "https://api.prembly.com"),
    "API_KEY": os.environ.get("PREMBLY_API_KEY", ""),
    "APP_ID": os.environ.get("PREMBLY_APP_ID", ""),
}
# BVN/NIN/vNIN verification is done by Wema's Full KYC (see utility.providers.verify_bvn
# / verify_nin / verify_vnin). Prembly above is retained ONLY for the image/biometric
# checks the number lookups can't do: the selfie/liveness step (kyc_verify_face — the
# ≥₦100k gate), address (kyc_verify_address), and ID-document OCR
# (kyc_verify_nin_document / kyc_verify_id_document).
# Card issuer (virtual cards) — generic provider; blank => mock mode.
CARD_ISSUER = {
    "BASE_URL": os.environ.get("CARD_ISSUER_BASE_URL", ""),
    "API_KEY": os.environ.get("CARD_ISSUER_API_KEY", ""),
    "BRAND": os.environ.get("CARD_ISSUER_BRAND", "Verve"),
}

# WhatsApp Cloud API (Meta). Runtime mode is explicit: disabled | sandbox | live.
# Production defaults to disabled when credentials are absent; local/test defaults
# to sandbox. This prevents an intentionally unconfigured production channel from
# becoming an unsigned public webhook.
WHATSAPP = {
    "MODE": os.environ.get("WHATSAPP_MODE", "").strip().lower(),
    "BASE_URL": os.environ.get("WHATSAPP_BASE_URL", "https://graph.facebook.com/v21.0"),
    "TOKEN": os.environ.get("WHATSAPP_TOKEN", ""),
    "PHONE_NUMBER_ID": os.environ.get("WHATSAPP_PHONE_NUMBER_ID", ""),
    "VERIFY_TOKEN": os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
    "APP_SECRET": os.environ.get("WHATSAPP_APP_SECRET", ""),
    "BUSINESS_NUMBER": os.environ.get("WHATSAPP_BUSINESS_NUMBER", ""),  # for wa.me deep links
    "ALLOW_CHAT_SIGNUP": False,  # resolved after _PROD is known below
}

# WhatsApp Flows — the secure PIN pad. When a published Flow ID + our RSA private
# key are set (and the WhatsApp channel is live), a money confirm sends an
# encrypted Flow whose masked PIN field never reaches the chat; the submit hits
# our data-exchange endpoint. Blank => the channel falls back to the single-use
# SMS confirmation code (verify-before-live: nothing changes until Meta business
# verification is done and these are set). The PRIVATE_KEY decrypts Meta's
# request; its public half is uploaded to the WABA once.
WHATSAPP_FLOW = {
    "FLOW_ID": os.environ.get("WHATSAPP_FLOW_ID", ""),
    "PRIVATE_KEY": os.environ.get("WHATSAPP_FLOW_PRIVATE_KEY", ""),
    "PRIVATE_KEY_PASSPHRASE": os.environ.get("WHATSAPP_FLOW_PRIVATE_KEY_PASSPHRASE", ""),
    "CTA": os.environ.get("WHATSAPP_FLOW_CTA", "Confirm with PIN"),
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

# Resolve and validate the WhatsApp runtime state now that production is known.
_wa_mode = WHATSAPP["MODE"]
if not _wa_mode:
    if WHATSAPP["TOKEN"] and WHATSAPP["PHONE_NUMBER_ID"]:
        _wa_mode = "live"
    else:
        _wa_mode = "disabled" if _PROD else "sandbox"
if _wa_mode not in {"disabled", "sandbox", "live"}:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("WHATSAPP_MODE must be disabled, sandbox, or live.")
if _PROD and _wa_mode == "sandbox":
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("WHATSAPP_MODE=sandbox is forbidden in production.")
WHATSAPP["MODE"] = _wa_mode
WHATSAPP["ALLOW_CHAT_SIGNUP"] = env_bool(
    "WHATSAPP_ALLOW_CHAT_SIGNUP", not _PROD and _wa_mode == "sandbox"
)

if _wa_mode == "live":
    from django.core.exceptions import ImproperlyConfigured

    required = {
        "WHATSAPP_TOKEN": WHATSAPP["TOKEN"],
        "WHATSAPP_PHONE_NUMBER_ID": WHATSAPP["PHONE_NUMBER_ID"],
        "WHATSAPP_APP_SECRET": WHATSAPP["APP_SECRET"],
        "WHATSAPP_VERIFY_TOKEN": WHATSAPP["VERIFY_TOKEN"],
        "WHATSAPP_BUSINESS_NUMBER": WHATSAPP["BUSINESS_NUMBER"],
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ImproperlyConfigured(
            "Live WhatsApp configuration is incomplete: " + ", ".join(missing)
        )

# --- Cache ------------------------------------------------------------------
# Rate limits, the idempotency-fallback bucket, and the WhatsApp throttle all sit
# on Django's cache, which is only correct across gunicorn workers when the cache
# is SHARED. Point CACHES at Redis by setting REDIS_URL; the default stays the
# in-process LocMem cache (single worker / dev) so no extra infra is required to
# boot. Tests always use LocMem for isolation.
REDIS_URL = os.environ.get("REDIS_URL", "").strip()
# A dedicated, isolated test deployment may deliberately exercise simulated
# provider flows with DEBUG off. Requiring this second explicit switch prevents
# a single stale simulation flag from silently turning off real money rails.
ALLOW_PRODUCTION_SIMULATION = env_bool("ALLOW_PRODUCTION_SIMULATION", False)
# The test-OTP bypass is refused on a live deploy for the same reason: one number
# signing in with a fixed code is an account takeover if the pair ever leaks. But a
# pre-launch deploy has a real need for it — SMS cannot deliver until a sender ID is
# carrier-approved, so without this there is NO way to sign in on the deployed host
# and the app cannot be walked end to end. Requiring a second explicit switch keeps
# the bypass a deliberate act rather than a stale env var nobody noticed. It does NOT
# make the deploy launch-ready: `wema_preflight` still HARD-FAILS on TEST_OTP, so
# go-live readiness cannot pass while the bypass is on.
ALLOW_PRODUCTION_TEST_OTP = env_bool("ALLOW_PRODUCTION_TEST_OTP", False)
# Multi-worker deployments must share rate-limit/idempotency state. Operators can
# opt out only for a deliberately single-process environment.
REQUIRE_SHARED_CACHE = env_bool("DJANGO_REQUIRE_SHARED_CACHE", _PROD)

from zitch_api.production_checks import validate_production_configuration

validate_production_configuration(
    is_production=_PROD,
    test_otp_phone=TEST_OTP["PHONE"],
    test_otp_code=TEST_OTP["CODE"],
    allow_test_otp=ALLOW_PRODUCTION_TEST_OTP,
    simulate_deposit_token=SIMULATE_DEPOSIT_TOKEN,
    wema_simulation=WEMA["SIMULATION"],
    mono_simulation=MONO["SIMULATION"],
    allow_simulation=ALLOW_PRODUCTION_SIMULATION,
    redis_url=REDIS_URL,
    require_shared_cache=REQUIRE_SHARED_CACHE,
)

if REDIS_URL and not TESTING:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }

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
        "standard": {
            "()": "common.logging.RedactingFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
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
        # These two are top-level logger names rather than living under "zitch",
        # so without an entry here they inherit root's WARNING and every log.info
        # they make is dropped before it reaches a handler. That silently cost us
        # the NUBAN provisioning trail in production — wema_account_provisioned
        # (which records whether an account came from the OTP flow or the bank's
        # callback), wema_bank_tier_synced, wema_credit_unsettled and
        # reserved_funding_duplicate have never once appeared in Render's logs.
        # ("zitch.security" needs no entry: a dotted name inherits its level from
        # "zitch" and propagates into that logger's handler.)
        "wallet": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "banklink": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
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
